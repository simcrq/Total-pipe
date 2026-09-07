---
name: total-pipe-deck
description: 从论文 PDF 到研究汇报 PPT 的三段式流水线（paperworkflow → pwf2rpa → research_ppt）。当用户要"把这篇论文做成汇报/组会 PPT""从 PDF 生成 slides""跑通 Total-pipe"时使用。
agent_created: true
---

# Total-pipe：论文 → 研究汇报 PPT

三段流水线，各段是一个 MCP 连接器。**本文件就是调用契约，不要再去读连接器源码。**

```
paperworkflow          pwf2rpa            research_ppt          render (slidep)        pptx-telemetry
PDF → 证据注册表  →  加 slide_briefs  →  选版面 / 规划    →    deck.pptx     →    OOXML 遥测 + QA
workflow.json          rpa_input.json       deck_plan.json                                 sidecar / QA 报告
```

| 段 | 连接器 | 位置 |
|---|---|---|
| 1 | `paperworkflow` | `F:/Workbuddy/Total-pipe/paperworkflow` |
| 2 | `pwf2rpa` | `F:/Workbuddy/pwf2rpa` |
| 3 | `research_ppt` | `C:/Users/Beibei/plugins/research-ppt-assistant`（symlink，真身 `F:/Project/PPTcreator/...`）|

版本：RPA **0.6.1**，版面库 **2.0.0**（320 版面 / 40 分类），pwf2rpa **1.0.0**。

> RPA 必须用 **Node.js 24**（固定 `D:/Node24/node.exe`，v24.20.0 Krypton LTS）。
> 不要硬编码 WorkBuddy managed node 的 `versions/<ver>/node.exe` 路径——升级会漂移。

> 三个连接器都必须在连接器管理页点过 **Trust** 才有工具可用。如果某个工具
> 不存在，先让用户去 Trust，不要去改代码。

---

## ⚠️ 第一次跑就要走完：不许挑着用命令

历史教训：RPA 有 20 个 CLI 命令 + 20 个 MCP 工具，实测第一次跑只用了 3 个就开工，
`deck_plan.json` 沦为一次性产物。**根因不是 Agent 偷懒，是渲染层不消费 deck_plan
——slidep 吃手写 SlideDSL，Agent 完全可以绕开规划。**

现在用「骨架生成器」把这条路堵死，见下面阶段 4 的硬约束。

### 阶段 3 必须按序跑完（缺一步不许进阶段 4）

**首选方式：一条命令跑完，别手敲：**

```bash
python "C:/Users/Beibei/.workbuddy/skills/total-pipe-deck/scripts/rpa_full_pipeline.py" \
    --rpa-input F:/x/rpa_input.json \
    --out-dir   F:/x \
    --project   F:/x/deck \
    --assets-from-slides        # 或 --assets-map 映射.json
```

它按 S1→S7 串行，任一步 FAIL 立即中断（退出码 1），每步产物落在 `<out-dir>/_rpa/`，
并生成 `_pipeline_report.md`。常用开关：

| 参数 | 用途 |
| :-- | :-- |
| `--assets-map m.json` | `{"4":["assets/fig1.png"]}` 显式指定每页用图 |
| `--assets-from-slides` | 从 `<project>/slides/NN.slide` grep 图片引用自动映射 |
| `--skeleton-out slides` | 默认 `slides_skeleton`（**不会覆盖正式页面**）；显式改 slides 才覆盖 |
| `--skeleton-force` | 骨架目录已有 .slide 时强制覆盖 |
| `--allow-visual-fail` | S5 几何不兼容仍继续（仅当手写 DSL 不按 RPA 槽位摆位时用） |
| `--strict` | warning 也当失败 |

S1–S7 分别是：normalize-content → plan → validate-deck → preflight →
visual-fit-preflight（每图）→ group-fit-preflight（多图页）→ deckplan2slide 骨架。

**S5 失败时脚本会直接给替代版面**（按图槽几何算 contain 填充率排序），照着把该页
brief 的 `category_hint` 改掉再重跑即可，不用自己翻版面库。

<details><summary>手工分步跑法（脚本出问题时才用）</summary>

```bash
NODE=D:/Node24/node.exe
RPA=C:/Users/Beibei/plugins/research-ppt-assistant
cd $RPA
$NODE server/cli.mjs normalize-content --file <rpa_input.json> --detail-level compact  > content_model.json
$NODE server/cli.mjs plan --file <rpa_input.json> --presentation-type group_meeting \
      --slide-count N --detail-level compact                                          > deck_plan.json
$NODE server/cli.mjs validate-deck  --file <deck_plan 与 content_model 合并后的 json>
$NODE server/cli.mjs preflight      --file preflight-input.json
```

`preflight-input.json` 模板（`deck_plan.slides` 必须填完整数组）：

```json
{
  "renderer_inputs": {
    "requested_renderer": "slidep", "renderer_version": "5.4.4", "platform": "win32",
    "project_path": "<PPT项目目录绝对路径>", "project_exists": true,
    "source_files": ["slides/01.slide"], "live_watch_requested": false
  },
  "deck_plan": { "theme_id": "paper_blue", "slides": [] }
}
```

`visual-fit-preflight` 最小输入（注意 `visual_container.content_bbox` 若给则必须
**等于** `allocated_visual_bbox`，否则抛 RangeError）：

```json
{
  "visual_id": "P04:visual",
  "source": {"width": 1440, "height": 1253},
  "source_region": {"x": 0, "y": 0, "width": 1440, "height": 1253},
  "visual_intent": {"visual_type": "dense_plot", "crop_policy": "full_figure",
                    "fit_policy": "contain", "priority": "primary_visual"},
  "visual_container": {"container_id": "visual"},
  "allocated_visual_bbox": {"x": 659.1, "y": 388.8, "width": 550.4, "height": 223.2}
}
```

`allocated_visual_bbox` = 版面 `slot_specs[].pptx_in` × 96（英寸→px）。

</details>

过关线：`pipeline_status == preflight_complete` 且 `status != invalid`，无重复 pageId。
页面带科研图必跑 `visual-fit-preflight`；同页多图有语义关系时补 `group-fit-preflight`。

### 阶段 4 只能从骨架开始（硬约束，这是关键机制）

```bash
python "C:/Users/Beibei/.workbuddy/skills/total-pipe-deck/scripts/deckplan2slide.py" \
  --plan deck_plan.json --rpa-root $RPA --out slides/ --node $NODE
```

- 没有 `deck_plan.json`，或 `pipeline_status != plan_complete` → **退出码 1，一个页面都不给生成**
- 输出 `slides/NN.slide`：slot 几何取自 `slot_specs[].pptx_in`（×96 转 px），按 `reading_order` 摆好，附 C 区页脚
- 输出 `slides/_slots.md`：每页每 slot 的 `max_chars` / `max_lines` / 字号 hint
- **填内容时严格照 `_slots.md` 的容量写**，超了会被判 `CAPACITY_EXCEEDED`

这套机制把 RPA 从「可跳过」变成「绕不过」：不跑 plan → 没有 layout_id → 没有骨架 → 无法渲染。
实测生成的 13 页骨架 `slidep-validate` 13/13 通过。

改页数时先改 `briefs.json` 的条数再重跑 plan（`--slide-count` 是目标不是填充），
然后删掉旧 slides/ 重新生成骨架。

---

## 阶段 1　paperworkflow：PDF → workflow.json

**1a. 列候选**（用户没指定论文时先调这个）

```
paperworkflow_prompt_builder        # 无参数
```
返回 `pdf_index`：`selection_id`（P001…）+ `relative_path`（相对 `INput`）。
把清单给用户选，**不要替他选**，也别把绝对路径念出来。

**1b. 跑工作流**

```
paperworkflow_literature_workflow
  source_path              # 必填，INput 相对路径或绝对路径，.pdf/.md
  queries                  # 可选，研究问题数组，≤12 条，每条 ≤2000 字符
  include_default_queries  # 默认 true
  top_k                    # 1-20，默认 5
  chunk_chars              # 500-20000，默认 6000
  output_dir               # 可选，项目内目录
```

产出 `workflow.json` / `document.manifest.json` / `evidence.md`。
**从返回值里取产物路径**，不要猜目录。这一步要 OCR，慢（分钟级），别重复跑。

判断能否进入下一阶段的依据是 **`synthesis_readiness`**（硬门禁），
不是 `quality_audit`（那只反映 OCR/排版可用性）。

**1c. 选完论文后回补**（第二次调 prompt_builder 时）

```
paperworkflow_prompt_builder
  selected_pdf          # 轮次一返回的 selection_id（P001）或 INput 相对路径
  research_goal         # 可选
  focus_questions       # 可选，字符串数组
  additional_context    # 可选
```
不传参数就是"列清单"模式，四个参数全可选。

**1d. 其余三个工具**（都已转换过 md 时很便宜，纯本地，不调模型）

```
paperworkflow_outline           markdown_path（必填）, chunk_chars
paperworkflow_search_evidence   markdown_path（必填）, query（必填）, top_k
paperworkflow_process_pdf       pdf_path（必填）      # 只 OCR，不建证据注册表
```

`paperworkflow_outline` / `search_evidence` 只收 `.md`，`process_pdf` 只收 `.pdf`；
且**路径必须落在 paperworkflow 项目目录内**，越界会被拒绝（这是它自带的沙箱）。

---

## 阶段 2　pwf2rpa：workflow.json → rpa_input.json

**2a. 先干跑**（不写文件）

```
pwf2rpa_check
  workflow_path    # 必填
  briefs_path      # 可选，不传则用证据的 query 意图自动兜底生成
  no_strict_fit    # 默认 false
```
返回 `slide_count` / `slides[]` / `warning_count` / `warnings[]`。

**2b. 确认无警告后再写文件**

```
pwf2rpa_convert
  workflow_path    # 必填
  out_path         # 默认写到 workflow 同级的 rpa_input.json
  briefs_path / strict / no_strict_fit
```

`paperworkflow_v4` 原样透传，这一段只补 RPA 推不出来的 `slide_briefs`。
输出逐字节稳定，同输入必同输出。

### 四条硬约束（README 说每条都真实咬过人）

1. `evidence_ids` 必须命中真实 `EV####` —— 否则 **error，阻断且不落盘**
2. `category_hint` 必须是 40 个合法 id 之一 —— 否则**不报错**，RPA 静默放宽成
   全库搜索、挑错版面（最阴的一种失败）
3. 文本要塞得进该分类的槽位 —— 否则 `SLOT_CAPACITY_EXCEEDED`
4. 输出必须可复现

**查合法分类**：`pwf2rpa_list_categories`（可传 `category` 只查一个），
返回每个分类的中文名、文本上限、图片上限、版面数、可用角色。
写 `category_hint` 前先查，别凭语感编。

`warnings[]` 里每条都带 `code` / `path` / `message` / `expected` / `hint` 和
渲染好的 `rendered` 一行 —— 直接照着 `hint` 改。

### 要带论文原图时：手写 briefs 的 `visuals`

兜底 briefs 的 `image_count` 全是 0，且 `question` / `chart_takeaway` /
`limitations` 等分类本身 `max_images=0` —— 要进图必须手写 briefs 并换成
`method_overview`(3图) / `dual_figure`(2图) / `figure_text`(1图) / `single_figure` /
`summary`(2图) 这类带图位分类。brief spec（README §Brief spec format，只有 `title` 必填）：

```json
{"category_hint":"dual_figure","title":"…","claims":["…"],
 "takeaway":"…","evidence_ids":["EV0014"],
 "visuals":[{"visual_type":"dense_plot","panel_count":1,
             "has_embedded_text":true,"caption":"图3：应力-应变曲线"}]}
```

**槽位陷阱（实测咬人）**：`visuals[].caption` 也占分类的 text slot。
`method_overview` 只有 2 个文本槽、`dual_figure` 只有 3 个（caption 槽仅 20 字）——
2 张图 + 2 条 claims + 1 条 takeaway 就会 `CAPACITY_EXCEEDED`。经验值：
每张图吃掉一个槽，caption 压到 ≤20 字，claims 每条 ≤34 字（dual_figure）/
≤56 字（method_overview），图多的页 claims 砍到 1–2 条。图片文件本身不进
rpa_input.json，只写 caption 元数据，真实文件在渲染阶段由渲染器从 assets/ 带入。

**正文字段 `body`（承载完整科学论证，不是 10 字标签）**：brief 可写 `body`
字段放整段正文（方法论证、结果解释等），它会绑定到该分类的 `text` 长槽
（如 `case`→`context`、`question`→`hypothesis`、`dual_figure`→`compare`、
`failure`→`cause`），而不是挤进 callout。三条约束：
1. `body` 计入 `text_chars`，整页文本别超该分类 `max_text_chars`，否则页面会被
   relax 到别的分类甚至整页丢失（theory 上限 170 字、background 150 字，见
   `pwf2rpa_list_categories`）。
2. 只写在有 `text`/`body_text` 槽的分类（background/theory/case/discussion/
   limitations/architecture 等）；`summary`/`cover` 无 text 槽，body 无处安放。
3. 每条 brief 还会自动带上 `evidence_texts`（所引证据的英文原文），供下游把
   证据展开成中文正文——这是参考材料，不直接上台面。

---

## 阶段 3　research_ppt：rpa_input.json → deck

**关键**：这两个工具收**内联 JSON 对象**，不读文件路径。所以要先读出
`rpa_input.json`，把它的两个 key 原样传进去。

```
normalize_content
  paperworkflow_v4: {...}    # 直接内联
  slide_briefs:     [...]
  detail_level: compact | standard | full   # 默认 compact
```
→ 得到规范化的 Source / Citation / Evidence（`SRC####` / `CIT####` / `EV####`）

```
create_deck_plan
  paperworkflow_v4: {...}
  slide_briefs:     [...]     # minItems 1, maxItems 40
  presentation_type: group_meeting | journal_club | proposal |
                     progress_report | defense | paper_presentation | custom
  slide_count          # 3-40
  audience / purpose / topic
  theme_id             # 默认 paper_blue
  viewing_mode         # 默认 projector
  density_preference   # 默认 balanced
  allow_auto_split     # 默认 true
  max_replan_attempts  # 默认 3
```

`normalize_content` 的 `paperworkflow_v4` 说明写的是 *"no file paths are read"* ——
传路径字符串是无效的，必须传对象。

### 文件一大就改用 CLI（很重要）

MCP 工具只收内联 JSON，而真实论文的 `rpa_input.json` 常在 50–100 KB 量级，
硬塞进工具参数会吃掉整屏 context。**默认走 CLI**，`--file` 直接吃 pwf2rpa 的产物：

```bash
cd C:/Users/Beibei/plugins/research-ppt-assistant
NODE=D:/Node24/node.exe
$NODE server/cli.mjs normalize-content --file <rpa_input.json> --detail-level compact
$NODE server/cli.mjs plan --file <rpa_input.json> \
      --presentation-type group_meeting --slide-count 6 --detail-level compact
```

- CLI 默认 `standard`，**加 `--detail-level compact`**，否则输出很长。
- `plan` 吃同一份 `rpa_input.json`（两个 key 都在），不需要先存 normalize 的结果。
- 只有当 `slide_briefs` 很少、文本很短时才值得用 MCP 工具内联。

实测基线：21 条证据 / 6 页的输入走 CLI，`normalize-content` 得
`status: valid`、`violations: []`；`plan` 得 `pipeline_status: plan_complete`、
`design_score: 0.865`。达不到 `valid` / `plan_complete` 就说明上游有问题，往回查。

---

> **RPA 自己不渲染 pptx**，但闭环是通的：它的 `server/renderer-adapters/` 里有
> `slidep` / `tencent-pptx` / `artifact-tool` 三个适配层，而 WorkBuddy 的
> **tencent-pptx** skill 引擎正是 slidep。所以阶段 4 能接上，往下看。
> （`rpa-pipe/fail-graphene/build_deck.py` 是单篇论文手写的 python-pptx 脚本，
> 不是通用渲染器，别拿它当第四段。）

---

## 阶段 4　渲染 pptx + 交付物遥测校验（pptx-telemetry）

> 下面裸 `node` 命令均指 `D:/Node24/node.exe`（RPA 必须 Node 24，见阶段 3 的
> `NODE` 变量定义），在 RPA 目录 `C:/Users/Beibei/plugins/research-ppt-assistant`
> 下执行。

完整工作流写在 RPA 的 `docs/USAGE.zh-CN.md` **§3（92–108 行）**，CLI 用法在
**§2（81–88 行）**，`preflight` 的输入结构在 §3 第 217 行起。
**格式吃不准就去这两处查，不要猜参数、不要编字段名。**

**4a. 渲染前**

```bash
node server/cli.mjs validate-deck --file deck_plan.json
node server/cli.mjs preflight     --file preflight-input.json
```

> **守卫已从"声明校验"升级为"接触真相"（0.3.3）**：
> - `preflight` 现在会**真实查磁盘**：`renderer_inputs.project_path` 与
>   `source_files` 里每个文件必须真实存在，否则返回 `invalid` +
>   `PAGE_SOURCE_FILE_NOT_FOUND`（跨机/无权限时降级为 `FILESYSTEM_VERIFY_SKIPPED`
>   warning，绝不假通过）。所以 preflight 的 `project_path` 必须填**渲染机上的真实
>   项目目录**、`source_files` 必须与磁盘实际文件名一致。
> - `validate-deck` 支持可选 `content_model`（normalize 的产物）：传入后会逐页重算
>   指标并交叉比对，产出 `metric_provenance`（`cross_checked_against_content_model`
>   或 `declared_not_verified`）与 `cross_check_divergences`。不传则如实标注为
>   `declared_not_verified`，不再把 plan 自报的 score 当结论。
> - `normalize_content` 额外输出 `coverage`（`orphan_must_keep_evidence` /
>   `unused_citations`），用于发现"声明必须保留却无任何页引用"的证据。

**4b. 渲染** —— 用 **tencent-pptx** skill（先读它的
`references/create-from-material.md`），以 `deck_plan.json` + `evidence.md`
为材料生成 pptx。产物目录里会有 `.pptx` 和 `slides/*.slide`。
论文里的图从 `images/` 按 evidence 的 figure 引用带进去 —— 兜底生成的
slide_briefs 不会自动带图，要进图必须自己指定。

**4c. 渲染后 shape 级校验** —— 用 **pptx-telemetry** skill（同仓库
`F:/Workbuddy/Total-pipe/skills/pptx-telemetry/`，符号链接在
`C:/Users/Beibei/.workbuddy/skills/pptx-telemetry`，完整契约/阈值/排错都在它自己的
SKILL.md 里）。

slidep / tencent-pptx **不导出** Render Telemetry，所以不能直接喂
`assemble-render-telemetry`（那需要渲染器给的 sidecar，现实中拿不到）。正确做法是
从**交付 pptx 的 OOXML 自己解析**出 shape 级实测证据（像素 bbox / 字号 / 颜色 /
溢出），再喂同一套 RPA QA 链：

```bash
PY=C:/Users/Beibei/.workbuddy/binaries/python/envs/default/Scripts/python.exe
NODE=D:/Node24/node.exe
TEL=F:/Workbuddy/Total-pipe/skills/pptx-telemetry/scripts

# 1) OOXML -> sidecar（--plan 注入真实 layout_id/category，推荐；重跑前先清空 sidecars/）
$PY $TEL/pptx_sidecars.py --pptx deck.pptx --out sidecars/ \
    --deck-id my-deck --plan deck_plan.json

# 2) 完整 QA 链：assemble → visual-quality 逐页 → 碰撞检测逐页 → validate-rendered-deck
$PY $TEL/run_qa.py --sidecars sidecars/ --work qa/ --node $NODE --plan deck_plan.json
```

产物 `qa/summary.json`：顶层 `deck.collision_invalid_slides` /
`deck.text_sparsity_invalid_slides` 等汇总，逐页 `collision_check` 与
`visual_quality` 明细。

关键阈值速查（改细节去 pptx-telemetry 的 SKILL.md，别在这里复制一份）：
对比度 <4.5 报 `TEXT_CONTRAST_LOW`、密集图宽 ≳0.4、行高模型 1.2em、srcRect 裁剪要
`preprocessed_fixed_region` 血缘、碰撞检测有含容过滤。缺 Pillow 会挂 sidecar 生成。

**备选（极少用）**：只有渲染器确实导出了 `render-evidence-sidecar.json` 时才走
RPA CLI 直连：

```bash
node server/cli.mjs assemble-render-telemetry --file render-evidence-sidecar.json
node server/cli.mjs visual-quality            --file render-telemetry.json
node server/cli.mjs validate-rendered-deck    --file rendered-deck.json
```

**诚实原则**：像素级校验需要 shape 级遥测（像素 bbox、字号、颜色、文本溢出）。
如果连 OOXML 解析都没做，**就明确报告「未做像素级校验」**——`not_evaluable`
等于"补充真实遥测，不能视为通过"，绝不能当绿灯。

## 输出怎么读

| 输出 | 关键字段 |
|---|---|
| `normalize_content` | `status`（应为 `valid`）、`violations`（应为 `[]`）、`source_count` / `citation_count` / `evidence_count` / `slide_brief_count` |
| `create_deck_plan` / `cli plan` | 顶层 `pipeline_status`（应为 `plan_complete`）、`production_status`、`design_status`、`design_score`、`deck_title`、`theme_id`、`slide_count`；每页在 `slides[].index` / `slide_id` / `category` / `layout_id` / `visual_treatment` / `aesthetic_score` |

健康基线：`status=valid` + `violations=[]` + `pipeline_status=plan_complete`。
不达标说明上游有问题，往回查，别在阶段 3 里硬调。

## 兜底：连接器还没被 Trust 时

pwf2rpa / paperworkflow 的工具不存在，通常只是用户没在连接器管理页点 Trust。
不想打断他的话，可以绕过 MCP 直接打 stdio 服务，**参数与 MCP 完全一致**：

```bash
printf '%s\n%s\n' \
'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"p","version":"0"}}}' \
'{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"pwf2rpa_convert","arguments":{"workflow_path":"...","out_path":"..."}}}' \
| C:/Users/Beibei/.workbuddy/binaries/python/versions/3.13.12/python.exe \
  F:/Workbuddy/pwf2rpa/integrations/mcp/server.py
```
paperworkflow 同理，python 换成 `F:/Workbuddy/Total-pipe/paperworkflow/.venv/Scripts/python.exe`。
结果在 `result.content[0].text` 里，是个 JSON 字符串；`isError: true` 表示失败。
不确定工具名就发一条 `{"method":"tools/list"}` 让服务自己报。
这只是临时兜底，正式用还是应该让用户去点 Trust。

## 排错

| 现象 | 原因 | 处理 |
|---|---|---|
| 工具不存在 | 连接器未 Trust | 让用户去连接器管理页点 Trust |
| `does not resolve to Evidence` | `evidence_ids` 里 id 拼错或不在注册表 | 对照 `workflow.json` 的 `evidence_registry` |
| 版面选得很怪、但没报错 | `category_hint` 不是合法 id | `pwf2rpa_list_categories` 查真名后改 |
| `CAPACITY_EXCEEDED` | 文本超出该分类最宽版面 | 缩短文本，或换容量更大的分类 |
| `CAPACITY_EXCEEDED` 报 `1 visual(s) but only 0 figure slot(s)` | `metrics` / `chart_takeaway` 两类版面**只吃图表、不吃图片**（`image_capacity=0`, `chart_capacity=1~2`） | 去掉该 brief 的 `visuals`，或换到支持图的 `experiment` / `background` / `method_overview` / `figure_text` |
| `CATEGORY_CROWDED` | 同分类里能装下的版面已被前面几页占完，RPA 不复用版面 | 缩短该页让更多变体可用，或换一个 category（实测把「良率均匀性」从 `metrics` 挪到 `method_overview` 即消解） |
| `visual-fit-preflight` 报 `visual_container.content_bbox must equal the inner allocated_visual_bbox` | `content_bbox` 与 `allocated_visual_bbox` 不一致 | 干脆**别传** `content_bbox`，只传 `outer_bbox` + `container_id` |
| `visual-fit-preflight` 报 `aspect_ratio_mismatch`、面积占比 <20% | 版面给的图槽长宽比与源图差太远（典型：流程类版面的窄条图槽塞方图） | 按脚本给的替代版面改该页 `category_hint`；或允许语义裁切时先跑 `figure-placement` 裁子图 |
| 读图尺寸报 "not png" / "unsupported image" | **扩展名不可信**：paperworkflow 抽出的图是 `.jpg` 内容，复制成 `assets/*.png` 后按扩展名解析必崩 | 按 magic bytes 嗅探（`\x89PNG` / `\xff\xd8`），别信后缀 |
| `preflight` 报 `NON_CANONICAL_PAGE_NAME` | 页面文件名只有两位序号（`01.slide`），缺语义后缀 | 改成 `01_cover.slide` / `02_method.slide`；页序稳定性依赖它 |
| briefs 加到 N 条但 `plan --slide-count N` 仍只出旧页数 | `--slide-count` 是**目标**不是填充，页数由 `rpa_input.json` 里的 briefs 条数决定 | 先把 `briefs.json` 补到 N 条 → `pwf2rpa_convert` → 再 `plan` |
| 副牌后段页数被挤掉 | RPA 不在同一副牌内复用了版面 | 减少同类短页，或降低 `slide_count` |
| `synthesis_readiness` 不通过 | 证据暴露/覆盖不足，不是 OCR 问题 | 回到阶段 1 补 `queries` 重跑 |
| 遥测报白字对比度 1.x，但肉眼看着没问题 | 文字框**几何溢出**了背后的色块（如徽章胶囊 26px 高、文字框 36px 高），背景解析回退到页底色 | 让色块显式 `height` + flex 居中，或加大内边距，**别改配色** |
| slidep validate 报 `CONTENT_OVERFLOW` | 常见触发：给徽章/胶囊里的 Text 加 `lineHeight: '<n>px'`（渲染器行高计算异常） | 去掉 px 行高，改在容器 Box 上定高居中；定位用**变量分离**（宽度改动 / 徽章改动分别单独 validate） |
| slidep upsert-dsl 报 `10201 Export file is occupied` | pptx 正被编辑器/预览占用 | 先 upsert 到 `_v2.pptx` 副本，再用 `cp` 覆盖原文件（cp 能成功） |
| 分隔线被判 `ORPHAN_DECORATIVE_ELEMENT` | 宽高比 ≥18 且面积占比 ≤0.02 的 decoration 元素 | 直接删掉分隔线、改用间距分组（加粗到 22px 才不算 thin，不划算） |

pwf2rpa 的容量模拟保真度 70/72，漏报方向是安全的（RPA 自己还会再告警），
所以**没警告不代表 RPA 一定完全满意**，但有了警告一定要先修。

## 版本对齐（改过任何一侧后做）

RPA 版面库升级后，pwf2rpa 的 `capacity.py` 会漂移：

```
pwf2rpa_refresh_capacity  { rpa_root: "C:/Users/Beibei/plugins/research-ppt-assistant" }
```
会重写 `capacity.py`，**跑完必须回归 `python -m unittest discover -s tests`（55 个）**。
平时核对：`pwf2rpa_list_categories` 返回的 `layout_library_version` 应与
RPA `assets/layout-library/layouts.json` 的 `version` 一致（当前都是 2.0.0，320 版面）。

### 给 RPA 发版时，版本号有 4 处必须同步改

漏任何一处都会红（`node scripts/check-version.mjs` 或 `node --test`）：

| # | 文件 | 字段 |
|---|---|---|
| 1 | `package.json` | `"version": "X.Y.Z"` |
| 2 | `.codex-plugin/plugin.json` | `"version": "X.Y.Z+codex.<YYYYMMDD>"` |
| 3 | `server/mcp-server.mjs` | `const SERVER_INFO = { ..., version: "X.Y.Z" }` |
| 4 | `tests/mcp.test.mjs` | `assert.equal(initialized.result.serverInfo.version, "X.Y.Z")` ← **最容易漏** |

改完跑：`node scripts/check-version.mjs` + `node --test`（当前 230 全绿）。
仓库有远端 `origin`，但**默认只 commit 不 push**，要推需用户明确说。

## 渲染层返工（加页 / 改结构）后的回补清单

在第 4 段（slidep 渲染）手工加页或大改结构后，**前 3 段的产物会集体过期**。不回补的话，
碰撞/对比度 QA 仍能跑（不依赖 layout_id），但规划与成品脱节，QA 摘要里会出现 `layout_id: None`。

每次加页后至少回补这 4 项：

| # | 回补项 | 命令 / 动作 | 不做会怎样 |
|---|---|---|---|
| 1 | 重跑 RPA plan 对齐页数 | `plan --slide-count <N>`（N = 实际页数） | `deck_plan.json` 停留在旧页数，QA 无布局基准 |
| 2 | 同步 `STORY.md` | 页面大纲 / rhythm 曲线 / 页数全部改成 N | story 与成品对不上，后续返工失去参照 |
| 3 | 同步 `DESIGN.md` | 「2.4 每页配色分配」「6. 母版组件清单」补到 N 页 | 新增页无配色分配约束，风格易飘 |
| 4 | 清理中间文件 | `_v2.pptx` / `_rebuild.pptx` / `deck_plan_cm.json` / `~$*.pptx` | 目录里堆几百 KB 无用文件 |

## 组件覆盖率门禁（收工前必跑，硬门禁）

光靠自觉扫一遍没用——实测会漏。**收工前必须跑脚本**，有 FAIL 就不许交付：

```bash
python "C:/Users/Beibei/.workbuddy/skills/total-pipe-deck/scripts/pipe_coverage_audit.py" \
  --project <PPT项目目录> --plan <deck_plan.json>
```

退出码 0 = 放行；1 = 有 FAIL。`--json` 可拿结构化结果。

它自动查 10 项：`pptx_pages` / `plan_pages` / `plan_freshness` / `story_pages` /
`design_pages` / `table_component` / `component_coverage` / `evidence_trace` /
`asset_usage` / `residue_files`。

手搭表格的检测特征是「深蓝表头行 + `borderTop:'none'` 拼出的斑马纹行」，
命中即 FAIL 并指出是哪几页。若要新增检测（比如别的组件漏用），直接往脚本里加 check 函数。

### 各段容易漏用的能力（跑完门禁后对照看）

- **paperworkflow**：只调 `literature_workflow` 就够出证据，但 `process_pdf` / `outline` / `search_evidence` / `prompt_builder` 常整段未用。若 `synthesis_readiness` 卡在 review 门，应回到这里补 `queries` 而不是硬过。
- **pwf2rpa**：默认兜底 briefs 常质量不佳（占位标题），人工重写后它就退化成**纯格式校验器**。重写 briefs 是合理的，但要清楚此时 `convert` 的自动映射价值≈0。
- **research_ppt**：有 20 个 MCP 工具 + 20 个 CLI 命令，通常只用 `normalize-content` / `plan` / `validate-deck` 三个。版面库 320 个版面、以及 `preflight` / `figure-placement` / `group-fit-preflight` / `visual-fit-preflight` / `visual-quality` 这一串**渲染前预检**经常被跳过——而它们恰好是防碰撞的第一道闸。
- **tencent-pptx**：组件有 14 个（box/text/image/**table**/chart/diagram/svg/faicon/math/codeblock/qrcode/hyperlink/animation/slide），实际常只用 4 个。表格类页面**务必用 `component-table.md` 的原生 Table**，不要用 Box+Text 手搭——手搭会被 QA 判 `EXCESSIVE_WHITESPACE`，且对齐难控。

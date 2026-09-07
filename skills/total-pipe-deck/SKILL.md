---
name: total-pipe-deck
description: 从论文 PDF 到研究汇报 PPT 的三段式流水线（paperworkflow → pwf2rpa → research_ppt）。当用户要"把这篇论文做成汇报/组会 PPT""从 PDF 生成 slides""跑通 Total-pipe"时使用。
agent_created: true
---

# Total-pipe：论文 → 研究汇报 PPT

三段流水线，各段是一个 MCP 连接器。**本文件就是调用契约，不要再去读连接器源码。**

```
paperworkflow            pwf2rpa                  research_ppt
PDF → 证据注册表    →    加 slide_briefs    →    选版面 / 规划 / 渲染
workflow.json            rpa_input.json           deck plan
```

| 段 | 连接器 | 位置 |
|---|---|---|
| 1 | `paperworkflow` | `F:/Workbuddy/Total-pipe/paperworkflow` |
| 2 | `pwf2rpa` | `F:/Workbuddy/pwf2rpa` |
| 3 | `research_ppt` | `C:/Users/Beibei/plugins/research-ppt-assistant`（symlink，真身 `F:/Project/PPTcreator/...`）|

版本：RPA **0.6.0**，版面库 **2.0.0**（320 版面 / 40 分类），pwf2rpa **1.0.0**。

> 三个连接器都必须在连接器管理页点过 **Trust** 才有工具可用。如果某个工具
> 不存在，先让用户去 Trust，不要去改代码。

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
NODE=C:/Users/Beibei/.workbuddy/binaries/node/versions/22.22.2/node.exe
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

## 阶段 4　渲染 pptx + RPA 后校验

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

**4c. 渲染后校验**

```bash
node server/cli.mjs assemble-render-telemetry --file render-evidence-sidecar.json
node server/cli.mjs visual-quality            --file render-telemetry.json
node server/cli.mjs validate-rendered-deck    --file rendered-deck.json
```

**诚实原则**：像素级校验（visual-quality / validate-rendered-deck）需要渲染器给出
shape 级遥测（像素 bbox、字号、颜色、文本溢出）。tencent-pptx 这次若没导出这些，
**就明确报告「未做像素级校验」**——RPA 文档自己写了 `not_evaluable`
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

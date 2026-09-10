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

它按 S1→S8 串行，任一步 FAIL 立即中断（退出码 1），每步产物落在 `<out-dir>/_rpa/`，
并生成 `_pipeline_report.md`。常用开关：

| 参数 | 用途 |
| :-- | :-- |
| `--assets-map m.json` | `{"4":["assets/fig1.png"]}` 显式指定每页用图 |
| `--assets-from-slides` | 从 `<project>/slides/NN.slide` grep 图片引用自动映射 |
| `--skeleton-out slides` | 默认 `slides_skeleton`（**不会覆盖正式页面**）；显式改 slides 才覆盖 |
| `--skeleton-force` | 骨架目录已有 .slide 时强制覆盖 |
| `--allow-visual-fail` | S5 几何不兼容仍继续（仅当手写 DSL 不按 RPA 槽位摆位时用） |
| `--no-design` | 跳过 S8（不推荐；跳过就等于回到"空旷"的旧行为） |
| `--min-font 10.5` | S8 字号下限，接管版面库的 18pt 约束 |
| `--density 22 44` | S8 每页元素数软目标区间 |
| `--strict` | warning 也当失败 |

S1–S8 分别是（**以 `rpa_full_pipeline.py` 的实现编号为准**，别按语感排）：

| | 步骤 | 说明 |
| :-- | :-- | :-- |
| S1 | `normalize-content` | → `content_model.json` |
| S2 | `plan` | → `deck_plan.json`，`pipeline_status` 必须 `plan_complete` |
| S3 | `validate-deck` | 必须 `valid` |
| S4 | `deckplan2slide.py` | 生成 SlideDSL 骨架（**必须先于 S5**，否则 preflight 报 `NO_PAGE_SOURCES`） |
| S5 | `preflight` | `preflight_complete` 且 `status != invalid` |
| S6 | `visual-fit-preflight` | 每张图不得 `fail` |
| S7 | `group-fit-preflight` | 每页 ≥2 图时查分组几何 |
| S8 | `design_land.py` | 出 `design_contract.json` + `_design_brief.md`。**只做准备，恒不 FAIL** |

S5 失败时脚本会直接给替代版面（按图槽几何算 contain 填充率排序），照着把该页
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

### S8 设计落地层（骨架 → 渲染之间的必经站）

**为什么必须有**：版面库契约每页只给 4–8 个槽、正文下限 18pt（实测见下方表格），
骨架忠实执行这份契约 → 每页 ~8 个大框、20pt 正文，**观感必然空旷**。
S8 把字号管辖权从 `layouts.json` 接到自己的 `design_contract.json`，
让 Agent 在已放行的槽位 bbox **内部**做排版细化与装饰落地。

契约关系（别搞反）：

| 契约 | 管辖范围 | 字号下限 |
| :-- | :-- | :-- |
| `layouts.json` | 版面选槽（S1–S7） | 18pt（实测 `{20:242, 18:78}`） |
| `design_contract.json` | 槽位内部排版（S8+） | 10.5pt（可 `--min-font` 改） |

两者不冲突：S5 preflight 已按 18pt 判过几何兼容并放行，S8 只在槽位内再切分，不再选槽。

```bash
DL="C:/Users/Beibei/.workbuddy/skills/total-pipe-deck/scripts/design_land.py"

python "$DL" contract --out design_contract.json --min-font 10.5 --density 22 44
python "$DL" brief --skeleton slides/ --slots slides/_slots.md --out slides/_design_brief.md
# —— 这里由 Agent 按任务书自由发挥，改写/扩写 slides/*.slide（构图不受脚本约束）——
python "$DL" check --slides slides/ --contract design_contract.json --out _design_check.md
# 图片资产不在默认位置时用 --assets 指路（可给多个；给「含 assets/ 的目录」或 assets/ 本身都行）：
#   python "$DL" check --slides deck/slides --assets deck/assets
```

`rpa_full_pipeline.py` 的 S8 已把 contract + brief 一并跑掉，所以一键跑完就有任务书；
**`check` 要等落地页写完再手动跑。**

**关键：脚本不代写设计。** `brief` 只给三类东西——

1. **事实**：每槽 bbox / 容量 / 已用字数 / 未覆盖的空白带 / 本页图片路径
   **+ 每张图的原始像素、图片比例、当前图框、建议图框、不改会裁掉多少**
2. **软目标**：密度区间（默认 22–44 个元素/页）、字号阶梯、可按需覆盖
3. **语汇货架**：顶栏标签 / 图注 / 脚注引文 / 指标双列 / 序号徽章 / 关键词高亮 /
   来源标注 / 对比条 / 流程箭头 / 分隔留白 —— **是"货架"不是"清单"，可全不用，可自创**

`check` 只卡**物理不可行**项（画布越界 / 字号低于下限 / 文字溢出 / 页脚与页码 /
**图片图框比例 ≠ 图片比例**），密度与构图只 WARN。`--strict` 才把 WARN 算失败。

#### ⚠ 图片：图框宽高比必须 = 图片文件自身比例（v1.1.0 起进 check）

**这是最容易被忽略、后果最直观的一条。** slidep 的 pptx 写出端对图片
**一律按 cover 裁切到图框比例**：

- `objectFit` 写 prop 也好、写在 `style` 里也好，填 `contain` / `cover` / `fill`
  —— 产出**逐字节相同**，属性完全无效（已用 5 变体探针实测）
- 唯一影响裁切量的是**图框自身的宽高比**：`裁切比 = 1 − min(框AR/图AR, 图AR/框AR)`
- 实例：`440×209` 的图（AR 2.11）放进 `1107×318` 的槽（AR 3.48）→ 上下各裁 **19.76%**，
  器件截面图的顶部标签与底部衬底**双双被切掉**

所以**「把图铺满槽位框」这个动作本身就是错的**。正确顺序是：

1. 读图片真实像素（`design_land.py` 自带 PNG/JPEG/GIF/BMP/WebP 头解析，纯标准库）
2. 按图片比例算 contain 适配矩形 `fit_rect(iw, ih, box_w, box_h)`
3. **让图框等于这个矩形**（contain 与 cover 在此时重合，歧义消失），在槽位内居中
4. 图卡 = 适配矩形 + padding；**腾出来的空档要用真实内容填**（参数表 / 指标行），
   而不是让大卡片空着 —— 否则 `element_area_ratio` 反而掉下来

`brief` 已为每张图算好建议尺寸，直接抄；`check` 对偏差 >2% 的图判
`IMAGE_ASPECT_MISMATCH`（FAIL）并给出应改成的具体尺寸。
`deckplan2slide.py` 的图槽注释里也会打印**槽位框的宽高比**供落地层比对。

> 规划器其实已经声明了 `allowed_transformations.preserve_visual_aspect = true`，
> 只是此前**没有任何一环执行它** —— S8 就是执行者。

**版面库实测天花板**（`research-ppt-assistant/assets/layout-library/layouts.json` v2.0.0）：

| 事实 | 值 |
| :-- | :-- |
| 每版面槽位数 | `{4:16, 5:112, 6:114, 7:68, 8:10}` → 单页最多 8 个元素 |
| 1864 个槽的 `font_pt_hint` | 全为 `None`（不规定细粒度字号） |
| `minimum_body_font_pt` | `{20:242, 18:78}` |
| `default_body_font_pt` / `default_title_font_pt` | 20 / 30（全部 320 版面） |

所以「空旷」不是 `deckplan2slide.py` 写错，是**整条快路径缺了 S8 这一站**。
参考成品（Windows）色板 `1E4FA8/4A5568/1A2230/D6DCE5/F7F9FC` 恰等于该脚本的
原始硬编码常量 → 它同样过了这座桥，只是桥后还跑了一次设计落地。**不是平台差异。**

**验收提醒**：`element_area_ratio` 的 warning 阈值是 <0.35、fail 才 <0.18，
**QA 全绿 ≠ 好看**。必须出图目视验收。

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

**4-1. 渲染前必须先过 S8 设计落地**

骨架是「一槽一框」的几何产物，直接渲染必然空旷。渲染前按 S8 流程走一遍：

```bash
DL="C:/Users/Beibei/.workbuddy/skills/total-pipe-deck/scripts/design_land.py"
python "$DL" brief --skeleton slides/ --slots slides/_slots.md   # 出任务书
#  ← 按任务书在全自由的前提下改写 slides/*.slide（详见阶段 3 的「S8 设计落地层」节）
python "$DL" check --slides slides/ --contract design_contract.json
```

`check` 退出码非 0 就别往下渲染。

> ⚠️ **中文渲染空白：落地页必须"一框一行"**（实测踩过，务必先读）
>
> slidep 的转换器**只在「内容放不进一个框」时**才写 `wrap="square"` + `<a:normAutofit/>`；
> 而 `normAutofit` 会让 LibreOffice / WPS 把**中文 run 渲染成空白**（对照实验：删掉
> normAutofit 中文立刻恢复）。单行框走 `wrap="none"` 分支，没有 normAutofit。
>
> 所以自写落地脚本时：**由脚本自己断行，每个 `<Text>` 只放一行**。断行必须
> **token 感知** —— 按字符贪心会把"不可分记号"拦腰斩断，实测出现过
> `Ωμ|m`、`EV00|61`、`TM|DC`、`MoS|₂`、`V_g(rea|d)`、`L| = 100 nm`。
> 正确做法：CJK 逐字切，**非 CJK 连续串整体不可分**（单位 / 缩写 / 证据号 /
> 下标上标 / `_ ( ) = + - / · →`），再把「数字 + 拉丁单位」胶合（`440 Ωμm` / `270 nm`）；
> 最后加中文**避头尾**：闭合标点（。，、；：）」）不得起行（放不下就把行末 token
> 一起挪下去），开括号不得落行尾。收尾用 `slidep-validate` 兜底（**别加**
> `--no-overflow-check`）。
>
> 验收门槛：交付 pptx 里 `<a:normAutofit/>` 计数应为 **0**、`wrap="square"` 应为 **0**。
>
> 另一条同源约束：**别把 `EV####` 印在台面上**。RPA 会判 `EVIDENCE_ID_LEAKED_TO_SLIDE`
> （「汇报页不应暴露内部溯源元数据」），而 `pipe_coverage_audit` 的 `evidence_trace`
> 又要求 slide 源里有 EV 号 —— 两条门禁看似冲突，**分层即可两全**：EV 号只写进
> `.slide` 的**文件头注释**，台面上放论文图号 / 主题词。实测 13 页泄漏 warning 清零，
> `evidence_trace` 仍 PASS。
>
> （机制说明：slidep 会把整份 `.slide` 源塞进 `p:cNvPr/@descr` 属性，属**元数据**、
> 不产生可见文本 —— 所以注释里的 EV 号既满足审计读源码，又不会被 QA 判泄漏。
> 已用 PDF 文本抽取 + `run_qa.py` 双向确认 14 页零可见泄漏。）

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
| `design_land.py brief` | `_design_brief.md`：每页槽位 bbox / 容量 / 已用字数 / **未覆盖空白带** / 图片 / 语汇货架。**没有"必须画什么"** |
| `design_land.py check` | 表头八列 `元素 / 最小字号 / 小字 / 越界 / 溢出 / 裁图`；`FAIL` 有 5 种码：`FONT_TOO_SMALL` / `OUT_OF_CANVAS` / `TEXT_OVERFLOW` / 页脚页码缺失 / `IMAGE_ASPECT_MISMATCH`（附「应改成 WxH」的建议）；`IMAGE_UNRESOLVED` 只 WARN |

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
| **成品"太空旷"**：文字飘在白底上，看不到卡片/竖条/装饰 | **根因是缺「设计落地层」，不是缺装饰。** 快路径 S4 出骨架 → 直接 `slidep start`，中间没有 S8 那一站。骨架忠实执行版面库契约（每页 4–8 槽、正文 18pt 下限）→ 必然空旷 | 补跑 S8：`design_land.py brief` 出任务书 → Agent 在槽位内做设计落地 → `check` 验收。详见「S8 设计落地层」节 |
| **图片被上下/左右裁掉一截**（图里的标签、图例、坐标轴没了；文字没丢，是画面没了） | slidep 的 pptx 写出端**对图片一律按 cover 裁切到图框比例**，`objectFit` 属性无效（prop/style × contain/cover/fill 五种写法产出逐字节相同）。把图铺满一个比例不同的槽位框 = 必然裁掉长边那一维。判据：pptx 里 `<a:srcRect>` 非零，且 `裁切比 = 1 − min(框AR/图AR, 图AR/框AR)` 完全对得上 | 让**图框宽高比 = 图片文件自身比例**：算 contain 适配矩形 → 图框取该矩形 → 在槽位内居中；图卡 = 适配矩形 + padding，腾出的空档用真实内容（参数表/指标行）填。`design_land.py check` 会判 `IMAGE_ASPECT_MISMATCH` 并给出应改尺寸 |
| 图片明明在框里居中留白了，`element_area_ratio` 反而掉下来 | 该指标只算**带文本元素 + 图片**的并集，纯色卡片不计；图按比例缩小后图片 bbox 变小 | 把图卡腾出的空档用**带文字的**内容填满（参数行 / 指标行 / 图注），别只留白 |
| 骨架的图槽注释写着"图槽宽度比 3.16"，但落地时找不到该塞什么比例的图 | `deckplan2slide.py` 只报槽位框比例，不知道你会用哪张图 | 先在 `briefs.json` 的 `visuals` 定好图（或落地层自己选图），再用 `design_land.py brief` 读真实像素算适配矩形 |
| pptx **只有形状、文字与图全空**（size ≈39KB、`ppt/slides/media` 为空、`<a:t>` 全空但 shapes 数正常） | `editor_sdk` 对**同一输出路径**反复 open/purge/commit 后会话变脏 | **换一个新的输出文件名**重渲即可（会话按 file_path 管理）。**不要 kill editor_sdk** |
| `slidep stop` 报 `nothing to stop: pid file not found`，日志里多个 pid 交替 removeSlide/addSlide、页数翻倍 | `rm -rf <project>/.slidep` 把 pid 文件一起删了，旧 daemon 仍在跑 → 多 daemon 互殴 | 清理顺序必须**先 stop 再 rm**；已乱则 `pkill -f slidep` 后单实例重启 |
| `slidep start` 报 `openFile network error: fetch failed` | `editor_sdk`（端口 39099，env `TENCENT_DOCS_LOCAL_MCP`）未运行。它是 WorkBuddy 宿主的本地编辑器服务，**没有自启命令** | 不要 kill 该进程；若已挂，等宿主自动拉起（实测约 30 分钟）后重试 |
| 封面必然报 `EXCESSIVE_WHITESPACE`（element_area_ratio <0.18 fail / <0.35 warn） | 该指标**只统计文字/图片元素的 bbox，纯色卡片不计入**，且不区分 category | 封面按"主视觉式"版面意图放一条关键成果带（大数字 + 小标签三档），而非一行小字 |
| 转出的 pptx 里中文**整段空白** | slidep 只对「放不进一框」的内容写 `wrap="square"` + `<a:normAutofit/>`，normAutofit 会让 LibreOffice / WPS 把中文 run 渲染成空白 | 落地脚本**自己断行**、每框一行（详见阶段 4 的「中文渲染空白」提示）。自查：pptx 里 `normAutofit` 与 `wrap="square"` 计数都应为 0 |
| 断行把单位/编号/下标**拦腰斩断**（`Ωμ\|m`、`EV00\|61`、`MoS\|₂`、`L\| = 100 nm`） | 按字符贪心断行，没有"不可分记号"概念 | 改 token 感知断行：CJK 逐字、非 CJK 连续串整体不可分，再胶合「数字+拉丁单位」，并加避头尾（闭合标点不起行） |
| 遥测报 `EVIDENCE_ID_LEAKED_TO_SLIDE`（台面出现 `EV####`） | RPA 认为汇报页不该暴露内部溯源元数据；但 `pipe_coverage_audit` 的 `evidence_trace` 又**要求** slide 源里有 EV 号，两条门禁看似冲突 | **分层**：EV 号只写进 `.slide` 的**文件头注释**（渲染器不输出注释），台面改放论文图号/主题词。实测 13 页泄漏 warning 清零，`evidence_trace` 仍 PASS |
| 主题化后出现 `ELEMENT_COLLISION`（卡片互相遮挡） | 版面库存在**层叠式版面**（如 RM-GAP-04 的 gap/ours 与 contribution 上下叠 79px），实体卡会判碰撞 | 与其它槽重叠 >10% 的槽不加卡片底（`slice_flags()`），并把文字改顶部对齐，避免落进邻卡 |
| **封面**一次报七条 `ELEMENT_COLLISION` | 封面也走了母版 `header()`：kicker 与页标题上下重叠 ~10px，且 y=112 的 `1140×1` 发丝线正好横穿主图卡与右侧指标卡 | 封面**关掉页眉**（`page(..., head=False)`，加一个 `head` 开关），只留眉标 + 主视觉标题；顺带消掉"标题重复" |
| 图注下半行被下方卡片盖掉（肉眼看是"字被切了一半"） | 图注框高 = `ceil(1.2×13) = 16`，放在图卡下缘 +8 处，而下方卡片上缘只留了 13.6px 空档 → 卡片白底盖住图注下半行（`fail`，ratio 0.10–0.81） | 图注框上边 = **图卡下缘 + 4**，且图注框下边 ≤ **下一元素上缘 − 2**；空档不够就把图卡压低 10–20px 腾出位置 |
| 结论条 / 尾句被页脚发丝线横穿（ratio ≈ 1.0） | 元素越过了 y=660 的页脚线；发丝线宽 1140 **不会**被含容过滤豁免（它比内容框宽） | 内容区下界收到 **656**；每页用构造期自检兜底（见下一条） |
| 落地脚本反复"渲染 → 遥测 → 改"效率很低 | 每轮都要跑 sidecar + QA 链 | 在落地脚本里**登记元素几何**（Box 与 Text 各记一条），用一份 `check.py` 复刻 `pairwise_collision` 的判定并估算 `element_area_ratio`，**构建期收敛到 0 FAIL** 再渲染。Text 的 bbox 口径见 pptx-telemetry SKILL.md 特例 6/7（居中的紧行框 `h=ceil(1.2×fontSize)`、`w`=外层 Box 宽） |
| 遥测 `EXCESSIVE_WHITESPACE` 卡在 0.18 线附近（如 0.178 fail） | 该指标**只统计带文本元素的并集**，纯色卡片、对比条、进度条一概不计 → 看板页的条形图撑不起来 | 补一行**带文本**的指标（顺便把页做完整），或加宽文本框；不要靠加装饰 |
| 弱化灰字被判 `TEXT_CONTRAST_LOW`（实测 4.08–4.27） | `#64748B` 在 `#EEF3FA` / `#E8EEF7` 上低于 4.5 触发线 | 弱化色加深到 `#4E5A70`（在各底色上 ≥5.96），配色语义不变 |
| 覆盖率门禁报 `plan_freshness` FAIL | `slides/` 的 mtime 晚于 `deck_plan.json`——S8 设计落地必然改 slides，这条**几乎必然触发** | **重跑 plan 再落盘**：`cd <rpa-root> && node server/cli.mjs plan --file <rpa_input.json> --presentation-type <pt> --detail-level <dl>`，与旧 plan 做 JSON **深比对**确认页数/score/版面/分类零差异后再覆盖。别只 `touch` |
| 覆盖率门禁报 `stage3_full` FAIL，但报告明明跑过 | `_pipeline_report.md` 落在 `<out-dir>/_rpa/`，而审计只看 `<project>/_rpa/` 与 `<project 父目录>/_rpa/` | 让 `--out-dir` 指向 **deck 的父目录**（或收工时把 `_rpa/` 挪到父目录） |
| 覆盖率门禁报 `story_pages` / `design_pages` WARN | 审计用 `### P\d+` 或 `\bP\d{1,2}\b` 抓页号，而 STORY/DESIGN 的页号列只写了 `01`/`02` | 页号列改成 `P01..Pnn` |
| `slidep script` 报 `Unknown command: script` | v6.1.0 里 script 是**独立可执行** `slidep-script`，不是 `slidep` 的子命令（`create` / `upsert-dsl` 才是子命令） | 用 `slidep-script -e "<code>" --file-path <pptx>`。脚本里 `Logger` 未定义会报错，但 `presentation.save()` 已先执行，exit 0 即视为已保存 |
| JSX 报 `Unterminated regexp literal` | 文本里有**裸 `>`**（如"耐久 >60,000"） | 改写成"耐久超 60,000"等不含 `>` 的表述 |
| slidep-export-images 报 504 / `upload credential failed` | 该命令依赖 docs.qq.com **在线**转换服务 | 非本地故障，稍后重试；验证结论以 QA 链为准，不要只靠导图 |

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

---

### 「太空旷」专节：断点在哪、怎么修

> 修法见上文「**S8 设计落地层**」节，这里只留断点定位的实测证据。

**断点**：阶段 3 的 S4（`deckplan2slide.py` 骨架生成）**之后**、阶段 4 渲染**之前**——
快路径缺 S8「设计落地层」。骨架是症状处不是根因；slidep 忠实渲染骨架，无责。

**症状 vs 参考**（同论文、同 13 页、同管线）：

| | 骨架（快路径） | 参考成品 |
| :-- | :-- | :-- |
| 元素/页 | ~8–12（13 页共 103 槽，见 `_slots.md`） | 28–49 shapes + 40–93 文本段 |
| 最小字号 | 20pt 正文 / 16pt meta | 10.5pt 图注、12pt 脚注引文 |
| 页脚 | 一整段 C 区 | 引文 + 分隔 + `Fig. 2e` 图注 |

参考成品色板 `1E4FA8 / 4A5568 / 1A2230 / D6DCE5 / F7F9FC` 恰等于 `deckplan2slide.py`
的**原始硬编码常量** → 它同样过了这座桥，只是**桥后还跑了一次设计落地**
（tencent-pptx 完整流程的 `DESIGN.md` 层）。**这不是 Mac / Windows 平台差异。**

**两条已排除的弯路**（别再走）：

- **治标无效**——只给骨架加主题 token / 6px 语义竖条 / 白卡圆角。装饰贴在 8 个大框上
  仍然空，密度问题一点没动。
- **绕过管线**——用 `ooxml2slide.py` 把一份达标成品转回 `.slide` 当模板。观感能一致，
  但这是拿成品反推，不补管线能力缺口，换个新论文立刻失效。

**验收提醒**：`element_area_ratio` 的 warning 阈值是 <0.35、fail 才 <0.18，
**QA 全绿 ≠ 好看**。封面 18.6% 只有 warning，照样放行。必须出图目视验收。

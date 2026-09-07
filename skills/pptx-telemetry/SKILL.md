---
name: pptx-telemetry
description: 对真实交付 pptx（slidep / tencent-pptx 等渲染器产出）直接做 shape 级遥测——解析 OOXML 实测证据 → 组装 0.4.7 sidecar → 跑 RPA QA 链（assemble-render-telemetry / visual-quality / validate-rendered-deck）。无孪生偏差，不依赖 artifact-tool。当需要校验「交付物本身」而非孪生版面、或渲染器不导出 Render Telemetry 时使用。触发词：真 pptx 遥测、交付物校验、pptx QA、slidep 校验、telemetry。
agent_created: true
---

# pptx-telemetry：真实交付 pptx 的 shape 级遥测

直接解析**交付 pptx 的 OOXML**，合成 artifact-tool layout/v4 形状喂给 RPA 的
artifact-tool 适配器，走同一套 QA 链——**度量对象就是交付物本身，无孪生偏差**。

定位与 `artifact-telemetry` skill（孪生渲染遥测）互补：那边是设计期拦截，这边是
交付物验收。两者结论不互相替代。

## 管线位置（slidep 渲染工作流）

```
slides/*.slide --slidep-start--> deck.pptx --pptx_sidecars.py--> sidecars/*.json
                                             --run_qa.py--------> QA 报告
```

## 依赖（换机器必查）

| 依赖 | 用到什么 | 缺了会怎样 |
|---|---|---|
| Python 3 + **Pillow** | pptx_sidecars.py（真实字体度量用 msyh.ttc；派生图裁剪用 Image） | sidecar 生成挂 |
| Node.js ≥18 | 跑 RPA CLI | QA 链挂 |
| research-ppt-assistant 插件 | `server/cli.mjs`（assemble/visual-quality/validate-rendered-deck） | QA 链挂 |

不依赖 artifact-tool。路径可迁移：`run_qa.py --rpa-root`、`--node`。

## 用法

```bash
PY=C:/Users/Beibei/.workbuddy/binaries/python/envs/default/Scripts/python.exe
NODE=C:/Users/Beibei/.workbuddy/binaries/node/versions/22.22.2-2/node.exe
SKILL=C:/Users/Beibei/.workbuddy/skills/pptx-telemetry/scripts

# 1) OOXML -> sidecar（--plan 注入真实 layout_id/category，可选但推荐）
$PY $SKILL/pptx_sidecars.py --pptx deck.pptx --out sidecars/ \
    --deck-id my-deck --plan deck_plan.json

# 2) QA 链（assemble -> visual-quality 逐页 -> pairwise-collision 逐页 -> validate-rendered-deck 整套）
$PY $SKILL/run_qa.py --sidecars sidecars/ --work qa/ --node $NODE --plan deck_plan.json
# --plan 可选：从 deck_plan 的 slot_assignments 提取声明正文，注入 validate-rendered-deck
# 的 declared_body，驱动「证据覆盖回归」(EVIDENCE_COVERAGE_GAP)。不传则只做几何/内容深度。
# 产出 qa/telemetry-XX.json、vq-XX.json、rendered-deck.json、summary.json
# summary.json 每页含 collision_check（status / checked_pairs / true_collisions），
# 顶层 deck.collision_invalid_slides / deck.collision_warning_slides，
# 以及 deck.text_sparsity_invalid_slides / deck.text_sparsity_warning_slides（P3 正文深度）。

# 正文深度检查（TEXT_SPARSITY，P3）：按 category 判定页面是否只有短标签碎片而缺成段论证——
#   content-heavy 分类(theory/case/discussion/limitations/method_overview/question/background)
#     最长文本块 <16 字=error、<40 字=warning；summary 总长 <30 字=warning。
#   由 validate-rendered-deck 在渲染页文本上自动判定，无需额外输入。

# 也可单独跑 pairwise-collision 检查单个 sidecar：
$PY $SKILL/pairwise_collision.py sidecars/slide-01.json
```

**重跑前必须清空 sidecars 与 work 目录**：run_qa 用 `split("-")[-1]` 取页号并
glob 整个目录，混入旧前缀文件会把同页跑两遍、deck 页数翻倍。

## 证据等级（逐字段披露在 synth doc 的 provenance）

- **measured_ooxml**：EMU→px 几何（÷9525，12192000×6858000 正好 1280×720）、
  srgbClr 色值、字号/加粗/字体、wrap/insets/lnSpc、srcRect 裁剪、图片
  sha256+原始尺寸、z 序
- **measured_meta**：slide 背景——slidep 把它藏在形状 `descr` 的 JSX 元数据里
  （`background: '#FFFFFF'`），能挖出来；挖不到按白底并披露
- **derived_metrics**：行数（真实字体 advance 宽度贪心换行 + PPT 1.2em 行高
  模型）与溢出（仅 wrap≠none 时判定）——**不是** PowerPoint 引擎自己的换行

## slidep pptx 特例（实测）

1. 媒体在 `ppt/slides/media/`（非标准 `ppt/media/`）；slide rels 的 Target 相对
   `ppt/slides/` 解析（`../media/x.jpg` → `ppt/slides/media/x.jpg`）。
2. 形状 name/descr 带 slidep 模板元数据；descr 里有 JSX 源（背景色来源）。
3. 文本普遍 `wrap="none"` 且按单行精确裁箱 → 行高模型必须用 1.2em，用
   asc+desc(≈1.32em) 会全页误报 TEXT_VISUAL_OVERFLOW。

## 硬契约（实测咬人）

1. **srcRect 裁剪必须走 `preprocessed_fixed_region` 血缘**：manifest 报
   `parent_asset_sha256` + `derived_asset_sha256`（用 Pillow 按真实字节裁出
   派生图算 SHA）+ `parent_source_region`；元素 `assetSha256`=派生哈希、
   `imageFit="contain"`、容器 `crop_policy="fixed_region"`。报 `fixed_region`
   会被 UNSUPPORTED_RENDERER_CROP 拦。
2. **manifest visual_key 必须与元素 `visual_key` 一致**，否则
   VISUAL_RENDER_ELEMENT_MISSING。
3. **不要声明 effectiveDisplayBbox**——装配器按 contain 确定论推导并要求
   1e-4px 一致，声明满框必挂 DISPLAY_GEOMETRY_MISMATCH。
4. **文字背景严格取元素 fillColor**（适配器无 z 序合成）：叠字元素把 z 序解析
   出的**最小包含面板**色写入 `fillColor`，加 `fill_provenance` 披露。不解析
   会把白字坐深蓝面板误报 1.0，也会漏掉白字坐浅色面板的真问题。注意取
   最小包含面板，取最大面积会选错层。

## QA 阈值（实测）

- `TEXT_CONTRAST_LOW`(error)：任何文字 <4.5:1。踩过的坑：#8B97A8 灰辅助字
  （2.81–2.96）、#3D7BD9 配浅底（4.17）、#D9534F 红字配 #E8EFF8（3.42）。
- `DENSE_FIGURE_TOO_SMALL`(error)：密集图显示宽/1280 归一值需 ≳0.4
  （0.4375 过、0.297 挂）。
- `EXCESSIVE_WHITESPACE`(warning)：封面留白大可接受。
- `SCIENTIFIC_VISUAL_NOT_EVALUABLE`(info) + assemble `manual_review_required`：
  图内 raster 字无像素度量，**不能当通过**——用 `slidep-export-images` 逐页
  PNG 眼检核对并如实报告（本 skill 无法机器闭环这一项）。
- `ELEMENT_COLLISION`(skill-local, **不在 RPA CLI 规则集内**)：pairwise
  element-bbox 碰撞检测，覆盖 RPA 缺的版面对撞规则。`scripts/pairwise_collision.py`
  跑在 visual-quality 之后，被 `run_qa.py` 自动调用。结果汇入每页
  `collision_check` + 顶层 `deck.collision_invalid_slides` /
  `deck.collision_warning_slides`。算法与阈值：
  - 跳过 slide-bg（≥1200×700 的全屏 rect）。
  - 跳过含容关系：一方 bbox 完全包含另一方（容差 0.5px）→ 视为文本坐容器内
    的正常 layering，不报警。
  - 阈值：warn = 重叠面积 ≥100px²；fail = 重叠面积 ≥200px² **且** 小 bbox
    被覆盖比例 ≥10%。
  - 实测覆盖到三类真实 bug：① 容器内错位/嵌套错（如 flexDirection='row'
    里塞 flexDirection='column' 子导致溢出 62px gap→0）；② caption 文字溢出
    卡片容器（既有 text_overflow 只对 slide 边界判，**容器内**溢出漏检）③
    装饰条压住正文字段（覆盖比例 >10% 即 fail）。
  - false-positive 控制：含容关系过滤 + 阈值双闸（面积+比例），
   `slidep-export-images` 渲染对比验证。

## 状态语义（不许美化）

`pass` 通过；`fail` 有已证实不一致；`blocked` 关键事实缺失；
`manual_review_required` = 事实完整但算法无法可靠判断 → 需 PNG 眼检或真实
栅格渲染核对，**绝不等于通过**。

## 排错

| 现象 | 原因 |
|---|---|
| UNSUPPORTED_RENDERER_CROP | 裁剪报了 `fixed_region`；要 `preprocessed_fixed_region` 血缘 |
| DERIVED/PARENT_ASSET_HASH_MISSING | 血缘字段缺；裁真实字节算派生 SHA |
| VISUAL_RENDER_ELEMENT_MISSING | 元素与 manifest 的 visual_key 不一致 |
| DISPLAY_GEOMETRY_MISMATCH | 声明了 effectiveDisplayBbox；删掉让 profile 推导 |
| 全页 TEXT_VISUAL_OVERFLOW | 行高模型不是 1.2em，或对 wrap=none 判了溢出 |
| 低对比误报 1.0 | 叠字元素没写 z 序解析的面板色到 fillColor |
| deck 页数翻倍 | sidecars 目录混入旧前缀文件；清目录重跑 |
| ELEMENT_COLLISION 误报 | 含容过滤未识别——可能 z 序分层（前景盖背景）但 bbox 不是父子关系；可调 pairwise_collision.py 的 CONTAIN_TOL_PX 或加 z 序豁免 |

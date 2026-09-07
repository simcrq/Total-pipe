---
name: artifact-telemetry
description: 用 artifact-tool 做孪生渲染 shape 级遥测（渲染期设计 QA，度量对象是 artifact-tool 重排版面而非交付 pptx）。校验交付 pptx 本身用 pptx-telemetry skill。触发词：遥测、telemetry、孪生渲染校验、visual-quality、not_evaluable。
agent_created: true
---

# artifact-telemetry：渲染后 shape 级遥测工具包

给任意 1280×720 版面产出 **真实渲染证据**：用 `@oai/artifact-tool`（本机
`F:/Workbuddy/artifact-tool`，v2.8.48）真实排版文字/图片，导出
`openai.presentation.layout/v4`（含真实字体度量、行数、溢出标志），再走
research-ppt-assistant 的 QA 链（assemble-render-telemetry → visual-quality →
validate-rendered-deck）。

> 背景：slidep（@tencent/slidep 6.1.0）**没有**遥测导出能力（dist 全文无
> telemetry/render_bbox/sidecar），tencent-pptx skill 渲出的 pptx 无法直接产
> 遥测。本工具用 artifact-tool 重排同一版面获得 shape 级证据——**它度量的是
> artifact-tool 的重渲染，不是 slidep 产物本身**，报告时必须如实声明。
> 要校验**交付 pptx 本身**（无孪生偏差），用 `pptx-telemetry` skill。

## 依赖（换机器必查）

| 依赖 | 用到什么 | 缺了会怎样 |
|---|---|---|
| artifact-tool 仓库 | **只需构建产物** `dist/artifact_tool.mjs`（含 dist/presentation-jsx 等），不需要源码 | render_layouts.mjs 无法渲染 |
| Node.js ≥18 | 运行 artifact-tool 与 RPA CLI | 两步都挂 |
| research-ppt-assistant 插件 | `server/cli.mjs`（assemble/visual-quality/validate-rendered-deck） | QA 链挂 |
| Python 3 | build_sidecars / run_qa（纯标准库，无 pip 依赖） | sidecar/QA 挂 |
| CJK 字体 | Microsoft YaHei（真实字体度量） | 中文行宽/行数失真 |

路径都可以指到别处：deck spec 的 `"artifact_tool"` 字段改 artifact-tool 根目录；
`run_qa.py --rpa-root` 改插件位置；`--node` 改 node 可执行文件。

## 真实 pptx 遥测

已拆分到独立 skill **`pptx-telemetry`**（`~/.workbuddy/skills/pptx-telemetry/`）：
直接解析交付 pptx 的 OOXML（无孪生偏差、不依赖 artifact-tool），契约细节见其
SKILL.md。本 skill 专注孪生渲染遥测。

## 失真边界（度量对象是孪生，不是交付物）

本工具的遥测对**孪生版面**是像素级真实的（真实字体度量、真实几何），但孪生与
真实渲染后端（slidep/tencent-pptx 产物）之间存在两类偏差：

- **手写偏差**：若孪生是手写 spec（非从 deck_plan/pptx 自动解析），几何与配色
  只凭人工对齐 —— 对孪生通过的结论**不自动等于**交付 pptx 通过。修孪生 ≠ 修交付物。
- **排版引擎偏差**：最终 pptx 由 PowerPoint/WPS 引擎排版，换行/kerning 与
  artifact-tool 的度量器可能不同 → lineCount/overflow 结论近似而非等同。

可迁移的结论（纯数学，不受引擎影响）：对比度（fg/bg 色值决定）、图片显示尺寸
归一值、frame 填充率 —— 前提是孪生与交付物用**同一套色值与尺寸**。
不可迁移的结论：具体 shape 的 bbox、行数、溢出 —— 只对孪生成立。

要校验**交付 pptx 本身**：用 `pptx-telemetry` skill（OOXML 实测证据 + 派生披露，
契约与 slidep 特例都已固化在那里）。

## 脚本

| 脚本 | 作用 |
|---|---|
| `scripts/render_layouts.mjs` | deck spec JSON → 逐页 layout/v4（真实字体度量） |
| `scripts/build_sidecars.py` | layout/v4 + assets → 0.4.7 sidecar（自动 SHA/尺寸/容器） |
| `scripts/run_qa.py` | 跑 assemble → visual-quality → validate-rendered-deck，汇总 |

```bash
NODE=C:/Users/Beibei/.workbuddy/binaries/node/versions/22.22.2-2/node.exe
# 1) 写 deck-spec.json（格式见 render_layouts.mjs 头注释）
$NODE C:/Users/Beibei/.workbuddy/skills/artifact-telemetry/scripts/render_layouts.mjs deck-spec.json
# 2) 组装 sidecar（SHA/尺寸自动算；容器自动找"包含图片的最小无字矩形"）
python C:/Users/Beibei/.workbuddy/skills/artifact-telemetry/scripts/build_sidecars.py \
  --layouts <layouts_dir> --assets <assets_dir> --out <sidecars_dir> --deck-id my-deck
# 3) QA 链
python C:/Users/Beibei/.workbuddy/skills/artifact-telemetry/scripts/run_qa.py \
  --sidecars <sidecars_dir> --work <work_dir>
```

## 硬契约（改版会咬人）

1. **manifest 必须存在**：即使无图页也要 `visual_manifest`（visuals 可为空数组），
   `manifest_schema_version` 必须是 **"0.4.7"**，且 `producer_version` / `deck_id`
   必填，否则 `blocked`。
2. **容器枚举**：`role ∈ image_only|image_caption|image_annotation|workflow_region|comparison_region|decorative_exhibit`；
   `crop_policy ∈ full_figure|semantic_crop_allowed|fixed_region`；
   `whitespace_policy ∈ minimal|intentional|reserved`；
   `fit_policy ∈ contain|cover`（须与渲染器 imageFit 一致）；
   `mismatch_policy ∈ replan|resolve_region|resize_container|allow_whitespace|change_fit_policy`。
   容器 `shape_name` 必须恰好匹配一个渲染元素。
3. **visual 必填事实**：slide_id（= layout 文档的 `slide.aid`，形如 `sl/xxxx`）、
   visual_type、panel_count、has_embedded_text、slot_id、container_id、
   crop_mode=full_figure + 完整 source_region、asset_sha256（用真实文件算）。
   SHA 不匹配 → `ASSET_IDENTITY_MISMATCH` fail。
4. **assetId 垫片**：artifact-tool 把资产 id 放在 `asset.assetId`，RPA 适配器读
   `element.assetId` —— build_sidecars.py 原样搬运，未杜撰任何值。
5. **visual_key 走 alt**：图片 `alt` 写 `rpa:<key> | 描述`，适配器据此关联 manifest。
6. **文字背景不做 z 序合成**：QA 的对比度检查用元素**自身** fill 当背景，底下
   垫的深色面板它看不见。白字叠在深色 rect 上 → 按 #ffffff 底算 1.0 必挂 error。
   修法：给文字形状补 `fill` = 底板同色（视觉不变，QA 可测）。
7. **PNG 预览不栅格化图片**：`slide.export({format:"png"})`（renderSlidePreview）
   对 image 元素只画浅灰**占位框**，不解码 JPEG —— 本地 PNG 可核对文字/配色/
   版面，**不能**用于图内 raster 字可读性核对（manual_review 无法本地闭环）。
8. **sidecar 文件名前缀要唯一**：run_qa.py 用 `split("-")[-1]` 取页号并 glob 整个
   `--sidecars` 目录。目录里混入旧前缀（如 `sidecar-01.json` + `slide-01.json`）
   会把同一页跑两遍、telemetry 互相覆盖、deck 校验页数翻倍。重跑前先清目录。

## QA 阈值（实测）

- `TEXT_CONTRAST_LOW`(error)：任何文字对比度 <4.5:1。踩过的坑：#8B97A8 灰脚注
  （4.17）、#1E4FA8 字配 #E8EFF8 底（2.96）、#D9534F 红字配浅蓝底（~3.0）。
  安全组合：深灰 #5F6B7A 以上做辅助字、深蓝字配浅蓝底、白字配深蓝底。
- `DENSE_FIGURE_TOO_SMALL`(error)：含嵌入文字的密集图，显示尺寸的
  min(宽,高)/720 或 宽/1280 归一值需 ≳0.4（0.4375 过、0.297 挂）。图片框
  尺寸按素材纵横比设置，别信"小图能看清"。
- `IMAGE_UNDERFILLED_SLOT`(error)：frame 与显示 bbox 填充率低 → 框尺寸=素材
  纵横比（fill_ratio=1.0）。
- `ORPHAN_DECORATIVE_ELEMENT`(error)：**细装饰条**（≤6px 高、无字、无关联）
  必挂。大色块卡片无字不挂（有 child relation 或面积）；别加 4px 顶饰条。
- `EXCESSIVE_WHITESPACE`(warning)：封面留白大可接受。
- `SCIENTIFIC_VISUAL_NOT_EVALUABLE`(info) + assemble `manual_review_required`：
  图内嵌字（raster text）无像素度量，**不能当通过**——按 recommended_action
  `inspect_raster_text_readability` 做人工/截图核对并如实报告。

## 状态语义（不许美化）

`pass` 通过；`fail` 有已证实不一致；`blocked` 关键事实缺失；`manual_review_required`
= 事实完整但算法无法可靠判断 → 需补充真实度量或人工核对，**绝不等于通过**。

## 排错

| 现象 | 原因 |
|---|---|
| McpInputSchemaError: not one of allowed values | 容器字段用了枚举外值（如 role:"evidence_frame"） |
| SHAPE_NAME_AMIGUOUS | 同名形状渲染了两个（如重复画顶条） |
| RENDER_ASSET_HASH_MISSING | 没跑 build_sidecars 的 assetId 垫片 / registry 键不对 |
| slide.export 不是函数 | import 路径错；必须 `dist/artifact_tool.mjs` |
| 中文行宽异常 | 字体缺 CJK；typeface 用 Microsoft YaHei |

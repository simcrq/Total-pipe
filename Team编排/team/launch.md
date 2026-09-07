# Total-pipe Team 编排手册（launch.md）

把 `team.yaml` 的骨架接到 WorkBuddy 的 Team 编排。这是运行时执行手册，配套 `prompts/` 下每个 agent 的最终 system prompt。

## 1. 现状与接法

WorkBuddy 当前对主 agent **未暴露显式 Team 工具**（实测 `TeamCreate` / `SendMessage` / `MessageColleague` / `SpeakInChannel` 均 Not found）。

真实通路是：**主 agent 用 `Agent` 工具 spawn 带 `name` 的 subagent，靠 `name + model + subagent_type` 三参数承载编排**。`name` 唯一、可被 `SendMessage({to: name})` 续接；`model` 对应智力档位；`prompt` 从 `prompts/` 读取（必须自包含，因为 subagent 看不到当前对话）。

**Orchestrator 由主 agent 兼任**（team.yaml 已注明：它只卡门槛不产内容，可降级为纯 if 代码）。因此实际 spawn 的是 4 个 subagent，不是 5 个。

## 2. Spawn 配置

| agent | name | model | subagent_type | prompt 文件 |
|---|---|---|---|---|
| 证据提取 | evidence-extractor | reasoning | general-purpose | prompts/evidence-extractor.md |
| 简报撰写 | brief-author | default | general-purpose | prompts/brief-author.md |
| 渲染 | renderer | default | general-purpose | prompts/renderer.md |
| 质量把关 | qa-inspector | reasoning | general-purpose | prompts/qa-inspector.md |

说明：
- `model` 映射智力档位：L3→reasoning、L2→default、L1→lite。
- `subagent_type` 统一 `general-purpose`（`Tools: *` 含 Read，能读图——两个多模态岗必须能看到图片）。
- 两个多模态岗（evidence-extractor、qa-inspector）是硬边界，用 reasoning 不可省。
- **renderer 用 default（L2），不是 lite**：它要展开 body 的科学论证、维护版式丰富度，属于语义取舍，lite 会压成标签碎片、洗成纯白。只有 orchestrator 可降级（由主 agent 兼任，或纯 if 代码）。

## 3. 编排顺序（串行流水线）

| 步 | 执行者 | 动作 | 依赖 | 产物 |
|---|---|---|---|---|
| 1 | evidence-extractor | 论文 → 证据 + 图映射 | 无 | evidence_registry + figure_map |
| 2 | brief-author | 证据 → 带图 briefs | 步1 figure_map | briefs.json |
| 3 | 主 agent（规则引擎，工具直调） | pwf2rpa_check 干跑 → convert → normalize → plan | 步2 | rpa_input.json + deck_plan.json |
| 4 | renderer | deck_plan → DSL → pptx | 步3 deck_plan | .pptx |
| 5 | qa-inspector | 遥测(规则) + 眼检(看图) | 步4 pptx | qa_summary |
| 6 | 修复循环 | QA 违规 → 回喂 renderer → 重跑遥测 | 步5 | 收敛到 0 error |

**诚实说明**：这条流水线是**串行**的，多 Agent 的价值不在"并行加速"，而在三件事——
1. 职责隔离：每个 agent 只做一件事，prompt 聚焦、不易跑偏；
2. 按岗配模型：看图岗用贵模型，其余用便宜模型，成本集中在刀刃上；
3. 交接物 schema 化：每个 agent 的输入输出都是带 schema 的文件路径，防"断真值"。

## 4. 交接契约（每条对应一次真实踩坑）

| 交接 | 必须保证的真值 | 踩过的坑 |
|---|---|---|
| evidence → brief | figure 引用显式带图，caption 落地 | 兜底 briefs 不带图，6 张图差点全丢 |
| brief → 规则引擎 | source_files 与磁盘文件名一致 | 假文件名 slides/01_cover.slide 曾判 valid |
| plan → renderer | layout/category 驱动渲染 | plan 只当参考，版式靠手写 DSL |
| renderer → qa | QA 吃真实 OOXML + 喂 declared_evidence | EV0008 丢失无人拦 |

## 5. 修复循环

QA 产出 error/fail 违规 → 明细回喂 renderer → 改 .slide DSL → slidep-validate → upsert → 重跑 pptx_sidecars + run_qa。循环直到 0 error / 0 fail，`max_rounds=5`，超限 block 报人工。

## 6. 门槛顺序（Orchestrator 机械执行）

pwf2rpa_check 干跑(0 warning) → convert → normalize(valid + violations=[]) → plan(plan_complete) → validate-deck → preflight(含文件系统真验证) → 渲染(slidep-validate 全 success) → 遥测 QA(0 error/fail) → 眼检闭环。

## 7. 占位符说明

prompts 里的 `{{pdf_path}}` / `{{images_dir}}` / `{{evidence_registry_out}}` / `{{briefs_out}}` / `{{deck_plan}}` / `{{pptx_out}}` / `{{qa_summary}}` / `{{declared_evidence}}` 是运行时变量，spawn 前替换成真实路径。这是"骨架"，不绑定具体论文，可复用。

## 8. 如何实际启动

1. 主 agent 读 `team.yaml` + 本手册，确认契约。
2. 替换占位符为真实路径。
3. 按第 3 节顺序，逐个 `Agent` spawn（步 1、2、4、5），步 3 主 agent 直调规则引擎工具。
4. 每步完成后校验该 agent 的门槛自检；fail 则 block 或回喂重试。
5. 全部通过后，`present_files` 交付 pptx + qa_summary。

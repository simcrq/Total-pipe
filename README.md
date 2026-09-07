# Total-pipe 工具链聚合仓库

论文 PDF → 研究汇报 PPTX 的完整工具链，四个组件集中在一个仓库里做版本管理。

## 目录结构

```
Total-pipe/
├── pwf2rpa/                    # 规则引擎（Python）：briefs → rpa_input → 容量门禁
│   ├── pwf2rpa/                # 包本体：briefs / capacity / convert / fit / workflow
│   ├── integrations/mcp/       # MCP server 入口
│   └── tests/                  # 59 项测试
├── skills/
│   ├── pptx-telemetry/         # 交付物遥测 QA（解析 OOXML → sidecar → 规则链）
│   ├── total-pipe-deck/        # 三段式流水线编排（paperworkflow → pwf2rpa → research_ppt）
│   ├── artifact-telemetry/     # （随附）孪生渲染 shape 级遥测
│   └── local-project-mcp-bridge/ # （随附）本地项目包装成 MCP 连接器
└── Team编排/                   # 多 Agent 编排骨架
    ├── team.yaml               # 角色 / 智力档位 / I-O 契约 / 门禁断言
    └── team/
        ├── launch.md           # 启动说明
        └── prompts/            # evidence-extractor / brief-author / renderer / qa-inspector
```

## ⚠️ 重要：仓库外有三处链接指向本仓库

本仓库是**唯一真身**。以下位置是符号链接，指向仓库内对应目录，修改任一侧都是同一份文件：

| 链接位置 | 指向 | 为什么必须存在 |
|---|---|---|
| `C:\Users\Beibei\.workbuddy\skills\pptx-telemetry` | `skills/pptx-telemetry` | WorkBuddy 从用户级 skills 目录加载技能 |
| `C:\Users\Beibei\.workbuddy\skills\total-pipe-deck` | `skills/total-pipe-deck` | 同上 |
| `F:\Workbuddy\pwf2rpa` | `pwf2rpa` | `~/.workbuddy/mcp.json` 以它为 cwd，加载 `integrations/mcp/server.py` |

因此**不要**把仓库目录挪走或改名，否则三处链接会断、MCP 与两个技能都会失效。

### 链接断了怎么重建

```powershell
# 以管理员或开启开发者模式运行；若 SymbolicLink 无权，把 -ItemType 换成 Junction
New-Item -ItemType SymbolicLink `
  -Path   "C:\Users\Beibei\.workbuddy\skills\pptx-telemetry" `
  -Target "F:\Workbuddy\Total-pipe\skills\pptx-telemetry"

New-Item -ItemType SymbolicLink `
  -Path   "C:\Users\Beibei\.workbuddy\skills\total-pipe-deck" `
  -Target "F:\Workbuddy\Total-pipe\skills\total-pipe-deck"

New-Item -ItemType SymbolicLink `
  -Path   "F:\Workbuddy\pwf2rpa" `
  -Target "F:\Workbuddy\Total-pipe\pwf2rpa"
```

配套备份仍保留在 `C:\Users\Beibei\.workbuddy\skills\*.bak`（链接验证通过后可自行删除）。

## 未纳入版本管理

`.gitignore` 排除了以下内容，均为运行产物或第三方工程，不是工具链代码：

- `Test1/` `Test2/` `Test3/` —— 各次运行的输入与产物（PDF、图片、pptx、sidecar、QA 报告）
- `paperworkflow/` —— 上游工程，390MB（含 venv）
- `.trash_20260907/` —— 迁移时隔离的**过期副本**，确认无需回滚后可 `rm -rf`
- `__pycache__/` `.slidep/` `.workbuddy/` `*.pptx` `*.7z` 等

> 注意：`skills/pptx-telemetry`、`skills/total-pipe-deck`、`pwf2rpa` 原先在 Total-pipe 下各有一份**过期副本**（缺 `body` 字段改动与渐变填充解析修复），已移入 `.trash_20260907/`，现由真身取代。

## 冒烟验证

```bash
# 规则引擎（59 项，用 unittest；本环境未装 pytest）
cd pwf2rpa && python -m unittest discover -s tests -q

# 遥测链路（对一份 pptx 出 sidecar + QA）
python skills/pptx-telemetry/scripts/pptx_sidecars.py --pptx <file> --out <dir> --deck-id <id>
python skills/pptx-telemetry/scripts/run_qa.py --sidecars <dir> --work <dir> --node <node> --plan <deck_plan.json>
```

## 相关仓库

- `research-ppt-assistant`（渲染/校验引擎，v0.6.1）位于 `F:\Project\PPTcreator\plugins\research-ppt-assistant`，是独立仓库，不在本聚合仓库内。

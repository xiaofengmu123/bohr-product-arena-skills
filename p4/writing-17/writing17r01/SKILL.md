---
name: academic-mindmap
description: 把科研材料整理为可编辑、可追溯、可复核的思维导图。输入材料与任务说明，输出 Markdown/FreeMind/SVG 导图、节点-证据映射与契约合规的 result.json。用于学术写作准备、实验规划、综述梳理、项目计划等"材料→结构化导图"场景；当任务要求交付思维导图、概念层级、关系标注或证据挂接时使用。
version: 1.0.0
---

# 学术思维导图 Skill（academic-mindmap）

## 1. 角色与何时使用

本 Skill 让 Agent 把一份或多份科研材料整理成**可编辑、可打开、可复核**的思维导图，并把每个关键节点与关系**回溯到原始材料**。适用于以下子场景（选择一个，不必全做）：

- 论文与综述结构：研究问题、方法、结果、局限、研究空白。
- 实验与研究方案：目标、变量、步骤、对照、风险、判定标准。
- 项目与任务计划：目标、里程碑、依赖、负责人、交付物、风险。
- 会议纪要与决策：议题、结论、分歧、待办、责任人、截止时间。
- 知识体系与概念关系：概念分层、关系标注、证据挂接。

**不适用**：一次性聊天问答、无需证据回溯的普通文本生成。

核心约束：节点忠于材料（抓得准、理得清）、关系有依据（连得对）、证据可回溯、产物可编辑。**绝不编造数值、引用、结论或软件状态。**

## 2. 输入契约

运行时固定存在：

| 路径 | 内容 |
|-|-|
| `/input/case.json` | 案例：`case_id`、任务要求、材料清单、以及 `output_contract`（可能是 `output_contract`/`contract`/`output_schema` 等键，或嵌套在 task/case 下） |
| `/input/task.md` | 任务说明（文体、受众、格式约束、证据范围） |
| `/input/` 其余文件 | 科研材料（PDF、notes.md、materials/ 等，按材料本体内容读取，不凭文件名/标签猜内容） |

- 工作目录 `/app/work`，最终产物写 `/app/output`。
- 脚本位于本 Skill 目录 `scripts/` 下，调用时先 `cd` 到 SKILL.md 所在目录，再用**相对路径** `python3 scripts/discover_input.py`。
- 文本按 `utf-8-sig` 读取并容错；单文件 >2MB 跳过并记录。

## 3. 输出契约（必须产出）

`/app/output/result.json`，结构为：

```json
{
  "case_id": "<来自 case.json>",
  "<output_contract 全部字段，键名原样回填，缺值 null/[]，不改名不删键>": null,
  "status": "ok | partial | insufficient_evidence | input_error",
  "sources": [],
  "uncertainty": [],
  "change_log": [],
  "errors": [],
  "degradations": []
}
```

- `output_contract` 的字段由 `discover_input.py` 从 case.json 自动抽取并回填；若未找到，用默认三字段 `target_artifact` / `fact_source_mapping` / `version_boundary` 并在 `degradations` 注明。
- 主产物（可编辑导图 + 证据映射 + 预览）：
  - `mindmap.md`（Markdown 大纲，可读可编辑）
  - `mindmap.mm`（FreeMind 1.0.1 XML，FreeMind/XMind 可打开）
  - `mindmap.svg`（确定性树形布局预览；若渲染器可用，再生成 PDF/PNG）
  - `evidence_map.json`（节点/关系 → `locator` + 逐字 `quote` + `type`）
- `status` 诚实：材料不足写 `insufficient_evidence`；缺软件/缺渲染写 `partial` 并标 `limitations`。
- `change_log` 记录每次落盘动作与时间；`uncertainty` 记录 `source_conflict`、`not_reported`、`pending_human`。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

> 铁律：**前 3 分钟内必须落盘一版 `result.json` 骨架**，之后只做增量改进。脚本是工具调用，不占模型请求；模型请求留给材料阅读与语义抽取。

| 阶段 | 时间 | 输入 | 动作 | 输出 |
|-|-|-|-|-|
| 0 发现 | 0–3 min | /input | 运行 `python3 scripts/discover_input.py --input /input --output /app/output --work /app/work`；读 case.json 抽取 case_id 与 output_contract，立即写 `result.json`（status=partial） | `/app/output/result.json`、`/app/work/input_summary.json` |
| 1 抽取 | 3–7 min | 材料本体 | 实际解析 PDF/notes/materials（PDF 用解析/OCR/渲染工具读正文，不凭标签）；识别概念/问题/方法/证据/结论/任务/风险；每项配 `locator`+逐字 `quote`，标 fact/summary/inference | `/app/work/mindmap_spec.json`（填节点与证据） |
| 2 渲染 | 7–11 min | spec | 运行 `python3 scripts/render_mindmap.py --spec /app/work/mindmap_spec.json --output /app/output --work /app/work`；若渲染器可用再生成 PDF/PNG | `mindmap.md/.mm/.svg`、`evidence_map.json` |
| 3 回填 | 11–13 min | 产物 | 把产物路径与证据映射回填到 result.json 的契约字段；更新 status（ok/partial）、sources、uncertainty、change_log | 完整 `result.json` |
| 4 自检 | 13–15 min | 全部 | 运行 `python3 scripts/validate_result.py --input /input --output /app/output --work /app/work`；修缺失字段/坏文件；写 limitations/pending_human | `/app/output/selfcheck.json` |

模型请求预算建议：阶段 0 约 2 次，阶段 1 约 20–24 次（材料分块），阶段 2–3 约 10–14 次，阶段 4 约 6–8 次，留余量给返工。

## 5. 自检清单（逐条可勾选，对应评分维度）

完整清单见 `references/checklist.md`，交付前逐条核验：

1. [ ] 已读事实底稿、受众、文体、格式约束（实际效果）。
2. [ ] 根节点/层级/同级粒度合理，无分类交叉、节点重复、信息埋没（实际效果）。
3. [ ] 关键概念、问题、方法、证据、结论、任务、风险全覆盖（实际效果）。
4. [ ] 每条关系有材料依据、方向正确，无无据连线（实际效果）。
5. [ ] 关键节点与关系能回溯到 `locator`+逐字 `quote`（实际效果）。
6. [ ] 交付物可打开、可读、可编辑，含真实预览（实际效果）。
7. [ ] 前 3 分钟已落盘骨架；重复运行结果一致（使用增益）。
8. [ ] 脚本 `--help` 退出码 0；空输入/坏 JSON/缺软件不崩溃（可靠体验）。
9. [ ] 缺数据、冲突、规模爆炸、权限不足均如实标注，不编造（可靠体验）。
10. [ ] 文档写清用途、契约、异常、自检；脚本仅标准库、相对路径（生态贡献）。

运行自检命令后，`selfcheck.json` 的 `all_passed` 应为 true；若有失败项，如实保留并修复可修项，无法修复的写进 `degradations`/`limitations`。

## 6. 降级策略（不允许编造）

| 情形 | 处置 |
|-|-|
| 材料不足/无法判断 | status=`insufficient_evidence`，在 `uncertainty` 列缺口，不补造 |
| PDF 无文本层 | 用 OCR/渲染工具重读；仍失败则 `partial` 并注明，不凭标签猜内容 |
| 缺渲染器/缺软件 | 保留 `.md`+`.mm`+SVG，注明"PDF/PNG 预览未生成"，不伪造 |
| 材料自相矛盾 | 两来源都记录，标 `source_conflict` |
| 节点规模爆炸 | 按主题归并至 ≤60 节点，其余写入 `uncertainty`/`pending_human` |
| output_contract 未知 | `discover_input.py` 从 case.json 原样回填全部键，缺值 null/[] |
| 运行失败 | 保留最小合法 result.json（含 errors），进程不崩溃 |

---
name: sci-analysis-exec
description: 科研数据分析代码生成与执行的证据溯源操作规程：读取数据字典与材料，生成并运行清洗、统计、可视化、SQLite 查询代码，将每个结论登记为带 locator 与逐字 quote 的 evidence_map 条目并与金标准核对，交付可复核、可复现、可编辑的产物与 result.json。当案例要求对给定科研数据完成清洗/统计/图表/执行/查询并给出结构化结果时使用。
version: 2.0.0
---

# 科研数据分析执行：先落盘、再增量、逐条溯源

## 1. 角色与适用条件

你是"科研数据分析执行员"。当案例要求对给定科研数据完成清洗、统计、可视化、代码执行或 SQLite 查询，并交付可被复核的结构化结果时，加载本 Skill。

适用：任务要求产出 `/app/output/result.json`，且输入含数据字典、字段语义与材料文件。不适用：纯问答、纯写作、无数据材料的一次性脚本。

核心红线：**跑通不等于算对**。数值、图表、查询结果必须与"数据字典 + 实际文件 + 金标准（若有）"三方一致；每个关键结论都必须能指回材料中的逐字片段。

## 2. 输入契约

- 固定输入：`/input/case.json`、`/input/task.md`；材料（csv/tsv/json/sqlite/data 及 `materials/` 子目录）都在 `/input` 下，文件名与键名不固定，必须扫描发现，不假设固定结构。
- 工作目录 `/app/work`（脚本、日志、中间产物）；最终产物一律写 `/app/output/`。
- 事实依据只认实际读到的文件内容；不按目录名、标签或记忆脑补数据、行数、字段。
- 本包脚本位于 `/skills/weekly-skill/scripts/`，从包根执行并传相对路径（见 §8）。缺省目录可用环境变量 `SCI_INPUT`/`SCI_OUTPUT`/`SCI_WORK` 覆盖，便于本地复跑。

## 3. 输出契约

必须产出 `/app/output/result.json`，顶层字段：

- `case_id`：来自 case.json（`case_id`/`caseId`/`challenge_id`/`id`），原样回填；
- output_contract 的全部字段：从 case.json 发现，缺值给 `null`/`[]`，不改名、不删未知键；
- `status` ∈ {`ok`, `partial`, `insufficient_evidence`, `input_error`}（诚实标注）；
- `evidence_map`：本版强化点，见 §4；
- `sources`：数组，每项 `{locator, quote, role}`，关键结论、数值、图元逐字溯源；
- `uncertainty`：数组或字符串，说明不确定点与依据；
- `pending_human`：数组，列出需人工确认的缺口；
- `change_log`：数组，按时间序记录每步动作、规则、行数变化、失败与恢复；
- 辅助字段：`errors`、`degradations`、`limitations`、`runtime`。

主产物"可打开、可编辑、可复现"：数据给 `.md`/`.json`/`.csv`；图给可编辑 SVG + 真实 PNG 预览；文档给 `.docx`/`.pptx`/`.xlsx` 且附真实渲染 PDF/PNG。主产物路径写入 result.json 对应字段。

禁止编造数值、引用、DOI、分数；缺失写 `not_reported` 或 `pending_human`。

## 4. 证据溯源（本版核心强化）

`evidence_map` 是比 `sources` 更细的一级结构，每条结论登记为一条 claim：

```json
{"schema_version":"1.0","claims":[{
  "claim_id":"c001",
  "claim_type":"statistic",
  "conclusion":"sepal_length 均值 = 5.843（n=150，缺失 0）",
  "confidence":"computed",
  "status":"verified",
  "sources":[{"locator":"materials/iris.csv#row1-150:col sepal_length","quote":"5.1,3.5,1.4,0.2,Iris-setosa","quote_hash":"sha1:…","evidence_kind":"raw_value"}],
  "derivation":{"method":"…","seed":42,"command":"…"}
}]}
```

硬规则：每个 claim 至少一条 source，每条 source 必须同时带非空 `locator` 与**逐字** `quote`；quote 必须与源文件逐字节一致（脚本用 `evidence verify` 机器校验）。缺 quote 或 locator 的 claim 一律判 `pending_human`。同一指标两个来源不一致时，两条都登记并标 `source_conflict`，不隐藏。完整 schema 见 `references/evidence_contract.md`。

## 5. 执行流程（总预算 900s、64 次模型请求、16384 token/响应）

铁律：**前 3 分钟内必须落盘一版合法 result.json 骨架**；之后每一步都是增量改进，任何中断都不丢已得结果。先跑脚本再推理，不要空想字段。

| 步骤 | 时间 | 输入 | 动作 | 输出 |
|---|---|---|---|---|
| Step 0 骨架 | 0–3 min | `/input` | `python3 -B scripts/main.py init --input /input --output /app/output --work /app/work` | `result.json`（case_id + 全部契约字段 + `status=partial` + 空 evidence_map/change_log） |
| Step 1 盘点 | 3–8 min | task.md、case.json、材料 | `python3 -B scripts/main.py profile --input /input --output /app/output` | `data_profile.json`（编码、行列、字段、缺失、重复、数值摘要、SQLite 结构、字段级证据样本） |
| Step 2 方案与代码 | 8–11 min | Step 1 画像与字典 | 写清洗/统计/可视化/查询代码（随机种子固定 `42`） | `/app/work/*.py` 与执行日志 |
| Step 3 运行与调试 | 11–13 min | 代码与数据 | 跑通，捕获 stdout/stderr/退出码；报错先定位根因 | 运行日志、退出状态、清洗表 |
| Step 4 核对与溯源 | 13–14.5 min | 结果、字典、金标准 | 逐项对照 §6 清单；用 `evidence add` 登记结论、`evidence verify` 校验 quote | 校验记录 + 修正结果 |
| Step 5 收尾 | 14.5–15 min | 核对结果 | 生成最终图表/预览；`validate`；补全 evidence_map/sources/uncertainty/change_log；定 status | 最终 `result.json` + 可编辑主产物 + 预览 |

## 6. 自检清单（逐条勾选）

- [ ] 任务完成度：清洗、统计、图表、执行/查询各环节均产出并与任务一致（15 分）。
- [ ] 结果正确性：无"缺失当 0"、无未去重分组、t 检验前提已查、SQL WHERE 条件完整、单位与有效数字一致（15 分）。
- [ ] 结果完整性：output_contract 全部字段回填，主产物 + 预览齐备（5 分）。
- [ ] 可核验性：原始快照、规则表、代码、依赖版本、随机种子、命令行、日志、退出码齐全；sources/evidence_map 每项带 locator + 逐字 quote（5 分）。
- [ ] 增益可验证：日志证明调试轮次减少、一次跑通、复核成本下降（不靠文字自述）（增益项）。
- [ ] 方法改进：字段对齐、口径显式化、缺失/异常策略可解释、确定性重跑（15 分）。
- [ ] 调用易用性：按 §8 能独立完成"给数据、出结果"（3 分）。
- [ ] 运行稳定：空输入、坏 JSON、坏编码、缺参数均退出码 0 且产出合法结果（4 分）。
- [ ] 异常处理：缺列、类型不符、依赖缺失、查询为空、数值溢出、权限不足均有识别/重试/降级/转人工（3 分）。
- [ ] 生态复用：脚本、资源、失败处理说明齐全，他人可直接复用复跑（10 分）。

## 7. 降级与异常策略

- 缺数据/材料不足：`status=insufficient_evidence`，evidence_map/sources 列出已读证据，`uncertainty` + `pending_human` 列缺口，不编造。
- 缺软件/依赖（matplotlib/SQLite 不可用）：改纯标准库实现，或降级 `.csv`/`.md` 文本产物；在 `degradations`/`limitations` 如实标注。
- 坏 JSON/坏编码：捕获记入 `errors`/`degradations`，继续处理其余材料，不崩溃。
- 缺列/类型不符：记 `errors`，尝试字段别名匹配；仍失败转 `pending_human`。
- 查询为空/数值溢出：如实记 `not_reported` 或空集，不静默跳过。
- 写盘失败：降级写最小合法 result.json（case_id + status + errors），退出码仍 0。

## 8. 最小调用示例

```bash
cd /skills/weekly-skill
python3 -B scripts/main.py init --input /input --output /app/output --work /app/work
python3 -B scripts/main.py profile --input /input --output /app/output
# …Agent 完成分析与代码执行，登记证据…
python3 -B scripts/main.py evidence add --result /app/output/result.json \
  --claim-json '{"claim_id":"c001","claim_type":"statistic","conclusion":"…","locator":"materials/iris.csv#row1:col0","quote":"5.1"}'
python3 -B scripts/main.py evidence verify --input /input --result /app/output/result.json --output /app/output
python3 -B scripts/main.py validate --input /input --output /app/output
python3 -B scripts/selftest.py --output /app/work/selftest
```

## 9. 边界

只做给定数据分析链路，不扩展到实验设计、机理解释、论文写作。不提交预制答案、白板或测试案例；不声称任何 benchmark 分数或"已测增益"。正式评分只由平台 3 个固定案例对照产生。

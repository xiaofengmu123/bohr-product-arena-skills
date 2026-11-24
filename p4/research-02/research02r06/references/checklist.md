# 评分维度对照与降级处置清单

本文档把题目评分维度映射为可勾选的自检项，并给出异常场景的标准处置。`validate.py` 自动覆盖其中"结构可机检"的部分，其余由 Agent 在 Phase 4 人工核对。

## 1. 评分维度对照

| 评分维度 | 分值 | 可机检部分（validate.py） | 需 Agent 人工核对 |
|---|---|---|---|
| 任务完成度 | 15 | 四类主产物齐全、status=ok | 结论与研究问题、证据范围一致 |
| 结果正确性 | 15 | claim→evidence 回链、强度枚举合法 | 每条 quote 逐字、强度与原文一致、文献真实 |
| 结果完整性 | 5 | 主题/冲突/空白字段非空（弱检查） | 主要主题、方法路线、正反证据与分歧全覆盖 |
| 结果可核验性 | 5 | 筛选标准先于决定、引用定位可回链 | 文献清单/筛选记录/证据表可独立复核 |
| 调用与易用性 | 3 | result.json 契约字段齐全 | 说明与脚本可独立复跑 |
| 运行稳定性 | 4 | 重复运行字节一致（selftest C） | 不同规模/质量/学科下仍可用 |
| 异常处理 | 3 | 空目录/坏 JSON 不崩溃（selftest A/B） | 缺全文/重复/冲突/超上下文有记录 |
| 生态贡献 | 10 | 脚本仅标准库 | 文档、证据表模板可复用与分享 |

## 2. 提交前逐条自检（对应 SKILL.md §7）

- [ ] `python3 -B scripts/selftest.py` 输出 `SELFTEST OK`；
- [ ] `python3 -B scripts/validate.py --output /app/output --case /input/case.json` 退出码 0；
- [ ] result.json 含 `case_id` + 全部 contract 字段 + `status/sources/uncertainty/change_log`；
- [ ] 前 3 分钟已落盘 `status=partial` 的 result.json（Phase 0 完成即满足）；
- [ ] 每条 claim 的 `evidence_refs` 都能找到 `evidence_id`，quote 为逐字原文；
- [ ] 冲突结论并列并标 `disputed`，研究空白落到具体证据缺口，非套话；
- [ ] 未报告项写 `not_reported`，不写成"不存在"。

## 3. 异常场景处置表

| 场景 | 处置 | 状态影响 |
|---|---|---|
| 空输入目录 | 写 `insufficient_evidence`，uncertainty 记录"无 case 输入" | 退出码 0 |
| 坏 JSON / 坏编码 | 捕获入 `errors`，其余材料继续处理 | `input_error` |
| 缺 `pdftotext` | 记入 `degradations`，用已给文本继续，不伪造解析 | 降为可完成度 |
| 仅有摘要/无全文 | 该篇标 `abstract_only`，结论降 `suggestive`/`not_reported` | 相应 claim 降级 |
| 元数据不全 | 写 `not_reported`；两源不一致时都记录并标 `source_conflict` | 记录而非择一 |
| 材料不足 | `insufficient_evidence`，uncertainty/pending_human 列缺口 | 不生成示例 |
| 未知/缺失字段 | 保留键填 `null`/`[]`，记入 `errors`/`degradations` | 不删键 |
| 超出上下文 | 分批抽取，每批结束增量写盘；claim 只引已读片段 | 不一次性倾倒 |

## 4. 禁止项

- 虚构文献、DOI、数值、评测分数或运行结果；
- 跨文档串值（把 A 文数值填进 B 文）；
- 把相关写成因果；
- 把摘要当全文证据；
- 用"背景/方法/挑战/展望"通用框架硬套主题（主题须从证据字段聚类生成）。

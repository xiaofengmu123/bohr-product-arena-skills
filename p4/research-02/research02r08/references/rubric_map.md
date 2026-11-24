# 评分维度 → 流程 → 自检项 映射（本题变体 #评分维度对齐）

本 Skill 的设计取舍：把题目公开评分维度**逐条**落到一个具体 Phase、一份具体产物和一条可勾选的自检项上，使“怎么写”由“怎么评”直接决定。评分维度见题面 `task.md` 第二节，此处为工程映射。

| 评分维度 | 分值 | 落实到的流程 | 承载证据的产物 | 对应自检项 |
|---|---|---|---|---|
| 实际效果·任务完成度 | 15 | Phase 1→2→3 四步全跑 | review.md / screening_log.json / evidence_table.jsonl / themes.md / conflicts_and_gaps.md | C1 |
| 实际效果·结果正确性 | 15 | Phase 2 证据抽取 + 校验器回源 | evidence_table.jsonl（quote 逐字回源） | C2 |
| 实际效果·结果完整性 | 5 | Phase 2 主题聚类 + Phase 3 冲突空白 | themes.md / conflicts_and_gaps.md | C3 |
| 实际效果·结果可核验性 | 5 | Phase 0 去重 + 每步增量写盘 + 版本记录 | inventory.json / dedup.json / change_log | C4 |
| 使用增益·质量/效率/可验证 | 25 | 流程先写标准、逐条回链、措辞分级，省人工补引用与重排 | evidence_table.jsonl + 强度措辞映射 | C5 |
| 创新增益·新增能力/方法改进 | 15 | 评分维度显式映射 + 防伪造 quote 回源 + 去重规则前置 | rubric_map.md + validate_result.py | C6 |
| 可靠体验·调用易用/稳定/异常 | 10 | 确定性脚本、容错读取、诚实 status、降级策略 | main.py / selftest.py / result.json | C7 |
| 生态贡献·文档/复用/分享 | 10 | 字段规范、脚本、示例外置 | SKILL.md / references/evidence_schema.md | C8 |

## 使用方式（写给评审/复现者）

1. 读 `references/evidence_schema.md` 获取证据字段与强度措辞规范；
2. 读本文件确认每条评分点由哪份产物与哪条自检项兜底；
3. 运行 `python3 -B scripts/selftest.py` 复现 6 项端到端自检（含空输入、坏 JSON、去重、确定性、防伪造 quote）。

## 与“通用综述流程”的差异（创新点边界）

- 通用流程把主题套成“背景/方法/挑战/展望”；本 Skill 要求主题从证据聚类（方法、对象、结论分歧）生长，并在 `themes.md` 写明每主题支撑 source_id 与边界。
- 通用流程的引用只是文末列表；本 Skill 要求正文 `[S#]` 逐条指向 `evidence_table.jsonl`，校验器把 `quote` 当作源文本子串做反向比对，**防伪造**。
- 通用流程对重复文献静默去重；本 Skill 在 `screening_log.json` 的 `dedup_records` 里写清“删了谁、因何删、保留了谁”。
- 通用流程把“未报告”写成“不存在”；本 Skill 用 `not_reported` 显式区分。

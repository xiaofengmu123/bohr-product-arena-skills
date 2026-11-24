# 自检清单与最小调用示例

本文件与 SKILL.md 第 5 节同源，交付前须逐条勾选；另附最小调用示例，示意输出仅为示例，非正式案例。

## 逐条检查（提交前全勾）
- [ ] 三段交付：claims / evidence_table / manuscript 齐备
- [ ] 每条论断有 claim_id，成稿中可定位；quote 逐字一致
- [ ] 每条 citation 真实、元数据准确、确实支持该论断（support 判定）
- [ ] source_kind / evidence_grade / support / grade_reason 四字段齐全
- [ ] 数字/术语/引用/图表/关键论断可回溯到 sources
- [ ] uncertainty 覆盖：冲突、仅摘要、机制未明、单篇小样本、相关≠因果
- [ ] pending_human 列出需人工确认项
- [ ] limitations 覆盖：缺全文、截断、格式不支持、缺软件
- [ ] change_log 含 scaffold/analyze/grade/finalize
- [ ] scaffold 不覆盖非 in_progress 结果；重复运行结果一致
- [ ] 未编造评测分数/基准/隐藏测试

## 最小调用示例（示例，非正式案例）
输入 `/input/case.json`：
```json
{"case_id":"demo-1","output_contract":{"deliverable":null,"sources":null,"change_log":null}}
```
`/input/materials/a.md`：
```text
研究甲（n=120）观察到 A 与 B 正相关（r=0.42）。
```
调用：
```bash
python scripts/main.py scaffold --case /input/case.json --input /input --out /app/output --work /app/work
python scripts/main.py extract --input /input --work /app/work
# Agent 依据材料写 /app/work/analysis.json，再把 claims/evidence 写入 result.json
python scripts/main.py finalize --case /input/case.json --work /app/work --out /app/output
python scripts/selftest.py --input /input --output /app/output
```
示意输出片段（勿当正式案例）：
```json
{"case_id":"demo-1","status":"complete","deliverable":{"claims":[{"claim_id":"C1","text":"A 与 B 呈正相关","type":"interpretation","verb":"相关","requires_citation":true}],"evidence_table":[{"claim_id":"C1","source_doc":"a.md","locator":"P1.s1","quote":"观察到 A 与 B 正相关（r=0.42）","source_kind":"primary","evidence_grade":"partial","support":"supports","grade_reason":"单篇 n=120，样本中等，仅相关"}]}}
```

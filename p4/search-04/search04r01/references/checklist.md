# 自检清单与评分维度映射

交付前逐条勾选，并运行 `python3 scripts/selftest.py`（退出码必须为 0）。

## 1. 结构完整性（对应「生态贡献·文档完整性」与「可靠体验·调用易用」）

- [ ] `SKILL.md` 有合法 YAML frontmatter：`name` / `description` / `version`。
- [ ] 写清角色与适用场景、输入契约、输出契约、执行流程、自检、降级策略。
- [ ] 脚本调用路径均为相对路径（`scripts/main.py`、`scripts/selftest.py`），可执行。
- [ ] 总文件数 5–8，总体积 60–140KB，`main.py` < 30KB。

## 2. 输出契约（对应「实际效果·结果完整性/可核验性」）

- [ ] `result.json` 含 `case_id`、`status`、题目 `output_contract` 全部字段（缺值 `null`/`[]`，不改键）。
- [ ] 含 `seed`/`seed_key`/`research_question`、`results.{papers,edges,clusters,paths,pruned}`、`sources`、`uncertainty`、`change_log`、`limitations`、`pending_human`、`errors`、`degradations`、`runtime`。
- [ ] `report.md`/`papers.csv`/`edges.csv`/`citation_graph.svg` 已由 `finalize` 生成且可打开。

## 3. 证据溯源（对应「实际效果·结果可核验性」，本版重点）

- [ ] 每条关键结论都有 `claim_id`。
- [ ] 每条 claim 在 `evidence_map.items` 有 `evidence_id` + `locator` + 逐字 `quote` + `quote_sha256`。
- [ ] `quote` 逐字来自材料，无改写；多段拆多条，不拼接伪造。
- [ ] 冲突信息两来源都登记并 `conflicts` 互指；缺失标 `not_reported`。
- [ ] 每条 `source` 带 `locator` 与逐字 `quote`。

## 4. 结果正确性（对应「实际效果·结果正确性」）

- [ ] 种子身份在材料中核实并写 `seed_key`。
- [ ] citation 边方向正确（`source` cites `target`），无猜测方向；关联边标 `undirected` 与 `basis`。
- [ ] 同文不同版本已合并，`merge_groups` 记录成员/键类型/键值。
- [ ] 排序按距离/被引/年份/来源置信度加权，不把“高被引”当“高相关”。

## 5. 完整性与方法（对应「实际效果·任务完成度」与「创新增益」）

- [ ] 覆盖关键被引出处、主要后续改进、代表性平行工作，或注明遗漏原因。
- [ ] 跨源身份对齐、方向感知扩展、可解释路径至少落地一项。
- [ ] 停止判据（层数 ≤3、剪枝、无新节点即停）已说明，剪枝写入 `results.pruned`。

## 6. 可靠体验（对应「可靠体验」）

- [ ] 空目录 / 坏 JSON / 坏编码 / 缺输入：退出码 0，写合法结果，不崩溃。
- [ ] 同输入两次运行产物一致（脚本确定性）。
- [ ] 种子无引文记录、同名歧义、跨库冲突、撤稿更正、限流/权限不足均已识别并写 `errors`/`degradations`/`pending_human`。

## 7. 降级与诚实（对应「可靠体验·异常处理」）

- [ ] 缺数据降 `insufficient_evidence`/`partial`，不补造文献、数值、引用、分数。
- [ ] 缺软件只降级不伪造：SVG 仍产出，PDF/PNG 缺省标 `preview_missing`。
- [ ] `change_log` 记录每次修改的时间 + 内容 + 原因。

## 8. 运行命令

```bash
python3 scripts/selftest.py
python3 -B scripts/main.py --help
python3 -c "import json;r=json.load(open('/app/output/result.json',encoding='utf-8'));assert r.get('case_id') and r.get('status');print('ok',r['status'])"
```

以上三条全部通过、退出码 0，方可交付。

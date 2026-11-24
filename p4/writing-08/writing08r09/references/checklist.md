# 自检清单与失败模式

## 1. 交付前逐条勾选（对应评分维度）

- [ ] 六类要素全部覆盖，无空节或占位空话（结果完整性 5 分）。
- [ ] 正文数值/参数/版本与材料逐字一致，无补数（结果正确性 15 分）。
- [ ] 每个事实性句子有 `[S#]`，`evidence_map.json` locator+quote 非空（结果可核验性 5 分）。
- [ ] `output_contract` 全部字段原样回填；`case_id`/`status`/`sources`/`uncertainty`/`change_log` 齐（输出契约）。
- [ ] 缺口显式标注：`MATERIAL GAP`、`pending_human`、`not_reported`，无静默编造。
- [ ] 冲突并列 + `source_conflict`；限制指向具体失效场景（而非"数据有限"类套话）。
- [ ] 主产物可编辑 + PDF/PNG 预览；`validate` 通过。
- [ ] `change_log` 完整：骨架→初稿→改进→收口，可回退。

## 2. 运行自检

```bash
python3 scripts/selftest.py        # 退出码 0
python3 scripts/main.py skeleton --input /input --output /app/output --work /app/work
python3 scripts/main.py inventory --input /input --output /app/output --work /app/work
python3 scripts/main.py validate  --input /input --output /app/output --work /app/work
```

`validate` 输出的 `validation_report.json` 中 `ok=true` 且无 `severity=error` 条目即通过。

## 3. 常见失败模式

| 失败模式 | 后果 | 对策 |
|---|---|---|
| 为段落顺畅补数值 | 结果正确性扣分 | 无来源写 `not_reported`，追问写 `pending_human` |
| train/val/test 条件串台 | 条件一致性扣分 | 三个子集分开写，逐条核对种子/版本/命令 |
| 只写主干不写细节 | 完整性扣分 | 按六要素覆盖表逐项勾选 |
| 证据 locator/quote 缺失 | 可核验性扣分 | 每句挂 `[S#]`，映射非空 |
| 冲突被隐藏 | 正确性/可信度扣分 | 并列记录 + `source_conflict` |
| status 冒报 ok | 可靠性扣分 | 缺材料/缺软件时如实 `partial`/`insufficient_evidence` |

## 4. 状态语义

| status | 含义 |
|---|---|
| ok | 六要素齐全，证据映射完整，无阻塞缺口 |
| partial | 部分要素已交付，存在明确缺口或异常 |
| insufficient_evidence | 材料不足，无法成稿，列出缺口不编造 |
| input_error | case.json 缺失或不可解析，仍产出合法骨架 |

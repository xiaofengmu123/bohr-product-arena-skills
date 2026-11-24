# 自检清单（对应评分维度）

提交前逐条勾选；收尾前在 Skill 根目录运行：

```bash
python3 -B scripts/main.py --help                 # 退出码 0
python3 -B scripts/selftest.py                    # 退出码 0，全部 PASS
python3 -B scripts/main.py scaffold               # 早落盘 partial 骨架
python3 -B scripts/main.py finalize               # 校验 + 补 runtime + 生成 report.md
python3 -B scripts/main.py validate               # 必须打印 VALID
```

## 输出契约完整性

- [ ] `/app/output/result.json` 存在且为合法 JSON。
- [ ] `case_id` 非空（缺则 `unknown` 并写 uncertainty）。
- [ ] `status` ∈ `ok | partial | insufficient_evidence | input_error`，且诚实。
- [ ] 回填 `case.json` 的 `output_contract` **全部**键（缺值给 `null`/`[]`，不改名、不删未知键）。
- [ ] 含 `sources`、`uncertainty`、`pending_human`、`change_log`、`limitations`、`runtime`。

## 证据溯源（本变体重点）

- [ ] 每条关键结论都在 `conclusions[]`，有 `conclusion_id`、`statement`、`strength`、`status`。
- [ ] 每条结论通过 `evidence_ids` 指回 `evidence_map` 中的证据。
- [ ] 每条证据含 `locator` + 逐字 `quote` + `quote_sha256` + `verification_status` + `locator_type`。
- [ ] `evidence`（扁平）与 `evidence_map`（键值）两种视图一致（`finalize` 自动 reconcile）。
- [ ] 冲突不隐藏：同一指标两来源不一致时并列记录并标 `source_conflict`。
- [ ] 无虚构引用/DOI/号码/状态；缺失写 `pending_human` 或 `not_reported`。

最小可复核样例（字段形状示意，非正式案例答案）：

```json
"conclusions": [
  {"conclusion_id": "C1", "statement": "公开号 US 12,345,678 B2 规范化为 US12345678B2，属授权公告(B2)。",
   "strength": "fact", "status": "supported", "evidence_ids": ["E1"]}
],
"evidence_map": {
  "E1": {"conclusion_id": "C1", "locator": "materials/scan1.pdf#p2", "locator_type": "pdf_page",
         "quote": "United States Patent 12,345,678 B2", "quote_sha256": "<64位hex>",
         "verification_status": "verified_verbatim", "source": {"engine": "uspto-scan", "retrieved_at": "..."}}
}
```

校验方式：`python3 scripts/main.py finalize` 会自动 `reconcile` 两种视图并计算 `quote_sha256`，再 `validate` 确认打印 VALID。

## 检索与核验可复跑

- [ ] 每条 `sources[]` 记录 `engine/query/url/retrieved_at/count/note`。
- [ ] 号码规范化与 `references/normalization-and-family.md` 口径一致。
- [ ] 同族标 `observed` 且 `exhaustive:false`。
- [ ] 法律状态含来源、查询日期、滞后风险；未知写 `unknown`。

## 鲁棒性

- [ ] 空输入目录：退出码 0，写 `insufficient_evidence`，不抛异常。
- [ ] 坏 JSON/坏编码：记入 `errors`/`degradations`，继续处理其余材料。
- [ ] 缺参数/未知参数：`--input/--output/--work` 缺省指向 `/input`、`/app/output`、`/app/work`。
- [ ] 重复运行：除 runtime 时间戳外，分析字段结果一致（无随机数，遍历顺序固定）。

## 交付标准

- [ ] 文件数 5–8 个、总体积 60–140KB、`main.py` < 30KB（库已拆到 `patentlib.py`）。
- [ ] 脚本只用 Python 标准库，路径用相对路径 `scripts/xxx.py`。
- [ ] 无预制正式答案、无伪造运行记录或分数。

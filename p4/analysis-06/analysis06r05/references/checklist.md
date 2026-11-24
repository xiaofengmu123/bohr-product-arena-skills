# 质量红线与自检清单（复核用）

本文件是 `SKILL.md` 自检清单的展开版，供 Agent 在收口阶段逐条核验、供评审复跑。

## 1. 四类输入场景的确定性要求

| 场景 | 期望行为 | 退出码 |
|-|-|-|
| 正常输入（DB/CSV + case.json + task.md） | 完整产出 report/schema/summary/correlation/figures/evidence，`status=partial` 或 `ok` | 0 |
| 空输入目录 | `status=insufficient_evidence`，`pending_human` 列缺口，不抛异常 | 0 |
| 坏 JSON / 坏编码 | 记入 `errors`/`degradations`，继续处理其余材料 | 0 |
| 缺参数 | `--input/--output/--work` 走默认 `/input`、`/app/output`、`/app/work` | 0 |

验证命令（在 skill 根目录执行）：

```bash
python3 -B scripts/main.py --help
python3 -B scripts/main.py --input /input --output /app/output --work /app/work
python3 -B scripts/selftest.py --input /input --output /app/output
```

## 2. 逐条红线

1. **契约逐一对齐**：`result.json` 顶层必须包含 `case_id` 与题目 `output_contract` 的**全部叶子字段**（缺值给 `null`/`[]`/`{}`，不改名、不删未知键），并以 `contract_fidelity` 记录逐字段填充状态。
2. **证据逐字**：`sources` 与 `evidence_map.json` 中每个结论都必须带 `locator`（SQL / 文件#表.字段）与逐字 `quote`，不允许只写结论。
3. **SQL 可复跑**：`query_log` 中每条 SQL 记录行数；连接始终只读（`mode=ro` + `PRAGMA query_only=ON`）；只允许 `SELECT/WITH/PRAGMA/EXPLAIN`。
4. **行数膨胀**：跨表查询必须显式写连接条件；GROUP BY 后行数必须能对应到连接键基数，否则标 `source_conflict`。
5. **编码型字段**：声明类型非数值、但值全为数字的列，标记 `coded_as_text` 并在 Python 侧转换，禁止直接对文本列用 SQL AVG。
6. **缺失/重复**：`NULL` 与空白视为缺失，逐列计数；全行重复用 GROUP BY 全列检测并记录组数与涉行数。
7. **口径一致**：SQL 聚合与 Python 重算做一次交叉核对（`summary_stats.csv` 与 `query_log` 中 `matches_python`），不一致必须显式说明。
8. **不编造**：没有数据、没有软件、没有执行记录的步骤，一律写 `null`/`pending_human`，不得填占位数值或伪造「已通过」。

## 3. status 判定参考

- `ok`：数据可查、contract 字段全部填充、无 errors、关键数值已复算一致。
- `partial`：有可交付结果但存在未覆盖维度、未填 contract 字段、可解释的降级或待人工项。
- `insufficient_evidence`：无 DB/CSV，或任务要求的证据完全缺失。
- `input_error`：case.json 等输入本身不可解析且无法继续（保留骨架）。

## 4. 交付前最终核对（提交 Skill 本身）

- [ ] `SKILL.md` YAML frontmatter 含 `name`/`description`/`version`，正文 ≤180 行。
- [ ] `scripts/*.py` 仅标准库，`python3 -B` 可运行，路径相对 `scripts/...`。
- [ ] 不包含 `__pycache__`、`*.pyc`、示例答案、预写正式测试产物。
- [ ] 总文件数 ≤14，总体积 ≤1.2MB；单文本文件 ≤500KB（平台上限 2MB/50 文件，本包更严）。

# 质量红线与自检清单（复核用）

本文件是 `SKILL.md` 自检清单的展开版，供 Agent 执行末段逐项核验、供评审复跑。

## 1. 四类输入场景的确定性要求

| 场景 | 期望行为 | 退出码 |
|-|-|-|
| 正常输入（SQLite/CSV + case.json + task.md） | 产出 schema/summary/correlation/figures/evidence，status=`partial` 或 `ok` | 0 |
| 空输入目录 | status=`insufficient_evidence`，`pending_human` 列缺口，不抛异常 | 0 |
| 坏 JSON / 坏编码 / 超 2MB 文本 | 记入 `errors`/`degradations`，继续处理其余材料 | 0 |
| 缺 `--input/--output/--work` | 走默认 `/input`、`/app/output`、`/app/work` | 0 |

验证命令（在 skill 根目录执行）：

```bash
python3 -B scripts/main.py --help
python3 -B scripts/main.py --input /input --output /app/output --work /app/work
python3 -B scripts/selftest.py
python3 - <<'PY'
import json
r = json.load(open('/app/output/result.json', encoding='utf-8'))
assert r.get('case_id') is not None or r.get('status') in ('insufficient_evidence', 'partial')
assert r.get('status') in ('ok', 'partial', 'insufficient_evidence', 'input_error')
assert 'sources' in r and 'uncertainty' in r and 'change_log' in r
print('ok', r['status'])
PY
```

## 2. 逐条红线（对应评分维度）

1. **证据逐字**：`sources` 与 `evidence_map.json` 中每条结论都必须带 `locator`（SQL 或 `文件#表.字段`）与逐字 `quote`，不允许只写结论。
2. **SQL 可复跑**：`query_log` 每条含 SQL 与行数；连接始终只读（`mode=ro` + `PRAGMA query_only`）；只允许 `SELECT/WITH/PRAGMA/EXPLAIN`。
3. **行数膨胀**：跨表查询必须显式写连接条件；脚本用 `join_row_check` 比较 LEFT JOIN 行数与左表行数，膨胀比 >1 标 `row_expansion`。
4. **编码型字段**：声明为文本但值全为数字的列标 `coded_as_text`，仅在 Python 侧 `float(strip())` 转换，禁止对文本列用 SQL `AVG`。
5. **缺失/重复**：`NULL` 与空白均视为缺失，逐列计数；全行重复用 `GROUP BY 全列 HAVING COUNT>1` 检测并记录组数与涉行数。
6. **口径一致**：SQL 聚合与 Python 重算做交叉核对（`aggregate.matches_python`），不一致必须写入 `uncertainty`。
7. **ID 列隔离**：名称形如 `id/*_id/*_key` 的主键/标识列参与 schema 与质量统计，但不进入相关矩阵与图表，避免把行号当测量值。
8. **不编造**：没有数据、没有软件、没有执行记录的步骤写 `null`/`pending_human`/`limitations`，不填占位数值、不伪造“已通过”。

## 3. status 判定参考（脚本自动执行）

- `ok`：数据可查、contract 字段全部非 `null`、无 `errors`、关键数值已复算一致。
- `partial`：有可交付结果，但存在未填 contract 字段、可解释的降级或待人工项。
- `insufficient_evidence`：无 SQLite/CSV 数据快照。
- `input_error`：输入不可解析且无法继续（保留骨架）。

脚本落盘的 status 只作初判；Agent 复核后可依据证据改判，但不得降级为“已通过”。

## 4. 交付前最终核对（提交 Skill 本身）

- [ ] `SKILL.md` YAML frontmatter 含 `name`/`description`/`version`，正文 ≤180 行。
- [ ] `scripts/*.py` 仅标准库，`python3 -B` 可运行，路径相对 `scripts/...`。
- [ ] 不包含 `__pycache__`、`*.pyc`、示例答案、预写正式测试产物。
- [ ] 总文件数 ≤14、总体积 ≤1.2MB；单文本文件 ≤500KB。

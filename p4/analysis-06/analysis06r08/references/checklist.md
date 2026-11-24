# 质量红线、SQL 模板与自检（复核用）

本文件是 SKILL.md 第 5、6 节的展开版，供 Agent 在执行末段逐项核验、供评审复跑。
所有红线以脚本真实产出为准，未执行的步骤不得写为「已执行」。

## 1. 四类输入场景的确定性要求

| 场景 | 期望行为 | 退出码 |
|-|-|-|
| 正常输入（DB/CSV + case.json + task.md） | 产出 schema/summary/correlation/figures/evidence，status=partial 或 ok | 0 |
| 空输入目录 | status=insufficient_evidence，pending_human 列缺口，不抛异常 | 0 |
| 坏 JSON / 坏编码 | 记入 errors/degradations，继续处理其余材料 | 0 |
| 缺参数 | `--input/--output/--work` 走默认 `/input`、`/app/output`、`/app/work` | 0 |

验证命令（在 skill 根目录执行，即 `/skills/weekly-skill`）：

```bash
python3 -B scripts/main.py --help
python3 -B scripts/main.py --selftest
python3 -B scripts/main.py --input /input --output /app/output --work /app/work
python3 - <<'PY'
import json
r = json.load(open('/app/output/result.json', encoding='utf-8'))
assert r.get('status') in ('ok', 'partial', 'insufficient_evidence', 'input_error')
for k in ('sources', 'uncertainty', 'change_log', 'query_log'):
    assert k in r and isinstance(r[k], list)
print('ok', r['status'], 'contract_keys=', len(r.get('contract_schema', {}).get('keys', [])))
PY
```

## 2. 逐条红线

1. **证据逐字**：`sources` 与 `evidence_map.json` 中每个结论都带 locator（SQL / 文件#表.字段）与逐字 quote，不允许只写结论。
2. **SQL 可复跑**：连接始终 `mode=ro` + `PRAGMA query_only=ON`；只允许 `SELECT/WITH/PRAGMA/EXPLAIN`；每条 SQL 记录行数。
3. **行数膨胀**：跨表查询先查 `schema.json` 的 `suggested_joins`，显式写连接条件；GROUP BY 后行数必须对应连接键基数，否则标 `source_conflict`。
4. **编码型字段**：`data_quality.csv` 中 `coded_as_text=true` 的列，声明类型非数值但值全为数字；须在 Python 侧显式转换后聚合，禁止直接对文本列 SQL AVG。
5. **缺失/重复**：NULL 与空白计为缺失，逐列计数；全行重复用 GROUP BY 全列检测，记录组数与涉行数。
6. **口径一致**：SQL 聚合与 Python 重算做交叉核对（`matches_python`），不一致必须显式说明。
7. **不编造**：无数据/无软件/未执行的步骤一律写 `null`/`pending_human`，不填占位数值、不伪造「已通过」。

## 3. status 判定参考

- `ok`：数据可查、contract 字段全部填写、无 errors、关键数值已复算一致。
- `partial`：有可交付结果，但存在未覆盖维度、未填 contract 字段、可解释的降级或待人工项。
- `insufficient_evidence`：无 DB/CSV，或任务要求的证据完全缺失。
- `input_error`：case.json 等输入本身不可解析且无法继续（保留骨架）。

## 4. SQL 模板（复用/复跑）

下述模板为示意，运行时表/字段名以 `/app/output/schema.json` 为准。

```sql
-- 单表聚合（编码字段先 CAST/显式转换）
SELECT grp, COUNT(*) AS n, AVG(CAST(code AS REAL)) AS avg_code
FROM "t" GROUP BY grp ORDER BY grp;

-- 全行重复检测
SELECT COUNT(*) FROM (
  SELECT * FROM "t" GROUP BY "a", "b", "c" HAVING COUNT(*) > 1);

-- 一对多安全连接（先确认连接键基数，避免膨胀）
SELECT p.id, COUNT(c.id) AS n_child
FROM "parent" p LEFT JOIN "child" c ON p.id = c.parent_id
GROUP BY p.id ORDER BY p.id;

-- 独立复算对照（Python 侧由脚本自动完成）
SELECT AVG("v"), MIN("v"), MAX("v"), COUNT("v") FROM "t";
```

## 5. 交付前最终核对（提交 Skill 本身）

- [ ] `SKILL.md` YAML frontmatter 含 `name`/`description`/`version`，正文 ≤180 行。
- [ ] `scripts/*.py` 仅标准库，`python3 -B` 可运行，路径相对 `scripts/...`。
- [ ] 不含 `__pycache__`、`*.pyc`、示例答案、预写正式测试产物。
- [ ] 总文件数 ≤14，总体积 ≤1.2MB；单文本文件 ≤500KB。
- [ ] `python3 -B scripts/main.py --selftest` 8 项全过。

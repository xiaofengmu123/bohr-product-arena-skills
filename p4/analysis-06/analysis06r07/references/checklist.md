# 质量红线与自检清单（复核用）

本文件是 SKILL.md 第 5、6 节的展开版，供 Agent 在执行末段逐项核验、供评审复跑。

## 1. 五类输入场景的确定性要求（鲁棒性核心）

| 场景 | 期望行为 | 退出码 |
|-|-|-|
| 正常输入（DB/CSV + case.json + task.md） | 产出完整 report/schema/summary/correlation/figures/evidence，status=partial 或 ok | 0 |
| 空输入目录 | status=insufficient_evidence，pending_human 列缺口，不抛异常 | 0 |
| 坏 JSON | 记入 errors（尽量 raw_decode 抢救前缀），继续处理其余材料 | 0 |
| 坏编码 | 按 utf-8-sig→utf-8→gb18030→latin-1 容错，记入 degradations，不崩溃 | 0 |
| 超大文件 | 单文本 >2MB 读前缀记 truncated；CSV 超 50000 行截断记 truncated_rows | 0 |
| 缺参数 | `--input/--output/--work` 走默认 `/input`、`/app/output`、`/app/work` | 0 |

验证命令（在 skill 根目录）：

```bash
python3 -B scripts/main.py --help
python3 -B scripts/main.py --input /input --output /app/output --work /app/work
python3 -B scripts/selftest.py
python3 - <<'PY'
import json
r = json.load(open('/app/output/result.json', encoding='utf-8'))
assert r.get('status') in ('ok','partial','insufficient_evidence','input_error')
assert 'sources' in r and 'uncertainty' in r and 'change_log' in r
print('ok', r['status'])
PY
```

## 2. 逐条红线

1. **证据逐字**：`sources` 与 `evidence_map` 中每个结论都带 locator（SQL / 文件#表.字段）与逐字 quote，不允许只写结论。
2. **SQL 可复跑**：`query_log` 中每条 SQL 记录行数；连接始终只读（`mode=ro` + `PRAGMA query_only=ON`）；只允许 `SELECT/WITH/PRAGMA/EXPLAIN`，多语句与写语句被 `guard_sql` 拦截。
3. **行数膨胀**：跨表查询必须显式写连接条件；GROUP BY 后行数必须能对应到连接键基数，否则标 `source_conflict`。
4. **编码型字段**：声明类型非数值、但值全为数字的列，标记 `coded_as_text` 并在 Python 侧转 float，禁止直接对文本列用 SQL AVG。
5. **缺失/重复**：`NULL`、空白与常见 NA 记号（NA/N/A/nan/null/none/-/?/missing）视为缺失，逐列计数；全行重复用 GROUP BY 全列检测并记录组数与涉行数。
6. **口径一致**：SQL 聚合与 Python 重算自动交叉核对（输出 `matches_python`），不一致记入 `uncertainty` 并显式说明。
7. **不编造**：没有数据、没有软件、没有执行记录的步骤，一律写 `null`/`pending_human`，不得填占位数值或伪造「已通过」。

## 3. status 判定参考

- `ok`：数据可查、contract 字段全部填写、无 errors、关键数值已复算一致。
- `partial`：有可交付结果但存在未覆盖维度、未填 contract 字段、可解释的降级或待人工项。
- `insufficient_evidence`：无 DB/CSV，或任务要求的证据完全缺失。
- `input_error`：case.json 等输入本身不可解析且无法继续（保留骨架）。

## 4. SQL 模板（复用）

```sql
-- 连接键基数核对：连接后行数不应高于主表行数 × 平均一对多倍数
SELECT COUNT(*) FROM fact f JOIN dim d ON f.key = d.key;

-- 编码型字段转数值（先显式转换，再聚合）
SELECT AVG(CAST(code_col AS REAL)) FROM t WHERE code_col GLOB '[0-9]*';

-- 缺失计数（NULL 与空白统一口径）
SELECT COUNT(*) FROM t WHERE col IS NULL OR (trim(col) = '');
```

## 5. 交付前最终核对（提交 Skill 本身）

- [ ] `SKILL.md` YAML frontmatter 含 `name`/`description`/`version`，正文 ≤180 行。
- [ ] `scripts/*.py` 仅标准库，`python3 -B` 可运行，路径相对 `scripts/...`。
- [ ] 不包含 `__pycache__`、`*.pyc`、示例答案、预写正式测试产物。
- [ ] 总文件数 ≤14，总体积 ≤1.2MB；单文本文件 ≤500KB。
- [ ] `python3 -B scripts/selftest.py` 退出码 0（9 类场景全过）。

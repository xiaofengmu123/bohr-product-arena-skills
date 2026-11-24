# 质量红线与自检清单（复核用）

本文件是 SKILL.md 第 5、6 节的展开，供 Agent 在执行末段逐项核验、供评审复跑。与本包配套的自检脚本为 `scripts/selftest.py`。

## 1. 四类输入场景的确定性要求

| 场景 | 期望行为 | 退出码 |
|-|-|-|
| 正常输入（DB/CSV + case.json + task.md） | 产出完整 report/summary/correlation/figures/evidence，status=partial 或 ok | 0 |
| 空输入目录 | status=insufficient_evidence，pending_human 列缺口，不抛异常 | 0 |
| 坏 JSON / 坏编码 | 记入 errors/degradations，继续处理其余材料 | 0 |
| 缺参数 | `--input/--output/--work` 走默认 `/input`、`/app/output`、`/app/work` | 0 |

验证命令（在 skill 根目录）：

```bash
python3 -B scripts/main.py --help
python3 -B scripts/main.py --input /input --output /app/output --work /app/work
python3 -B scripts/selftest.py
python3 - <<'PY'
import json
r = json.load(open('/app/output/result.json', encoding='utf-8'))
assert r.get('status') in ('ok', 'partial', 'insufficient_evidence', 'input_error')
for k in ('sources', 'uncertainty', 'change_log', 'query_log', 'errors'):
    assert k in r, k
print('ok', r['status'])
PY
```

## 2. 逐条红线

1. **证据逐字**：`sources` 与 `evidence_map.json` 中每个结论都必须带 locator（SQL / 文件#表.字段）与逐字 quote，不允许只写结论。
2. **SQL 可复跑**：`query_log` 中每条 SQL 记录行数；连接始终只读（`file:...?mode=ro` + `PRAGMA query_only=ON`）；只允许 `SELECT/WITH/PRAGMA/EXPLAIN`，其余语句直接拒绝并记 error。
3. **行数膨胀**：跨表查询必须显式写连接条件；GROUP BY 后行数必须能对应到连接键基数，否则标 `source_conflict`。`route_and_execute` 会解析 FROM/JOIN 引用的表并把查询路由到含全部引用表的唯一数据源；路由歧义记为 error，不静默选库。
4. **编码型字段**：声明类型非数值、但值全为数字的列标记 `coded_as_text`，聚合用 `CAST(col AS REAL)` 并在 Python 侧重算，禁止直接对文本列用 SQL AVG。
5. **缺失/重复**：`NULL` 与空白视为缺失，逐列计数；全行重复用 `GROUP BY` 全列 `HAVING COUNT(*)>1` 检测并记录组数与涉行数。
6. **口径一致**：数值列的 SQL 聚合与 Python 重算自动对照，输出 `matches_python`；不一致必须显式写入 `uncertainty`。
7. **不编造**：没有数据、没有软件、没有执行记录的步骤，一律写 `null`/`pending_human`，不得填占位数值或伪造「已通过」。auto-fill 仅当 contract 键与单一列/统计精确匹配（confidence=high）才写值，其余留 `null` 并列入 `pending_human`。

## 3. status 判定参考

- `ok`：数据可查、contract 全部字段非 null、无 errors、关键数值已复算一致。
- `partial`：有可交付结果但存在未覆盖维度、未填 contract 字段、可解释的降级或待人工项。
- `insufficient_evidence`：无 DB/CSV，或任务要求的证据完全缺失。
- `input_error`：case.json 等输入本身不可解析且无法继续（保留骨架）。

## 4. 交付前最终核对（提交 Skill 本身）

- [ ] `SKILL.md` YAML frontmatter 含 `name`/`description`/`version`，正文 ≤180 行。
- [ ] `scripts/*.py` 仅标准库，`python3 -B` 可运行，路径相对 `scripts/...`。
- [ ] 不包含 `__pycache__`、`*.pyc`、示例答案、预写正式测试产物。
- [ ] 总文件数 ≤14，总体积 ≤1.2MB；单文本文件 ≤500KB；main.py <30KB（已拆 core.py）。

## 5. 与参考包的关键差异（本包取舍）

1. **校验优先**：先落盘合法骨架、后做任何分析；计算（`compute_analysis`）与渲染（`emit_artifacts`）解耦，中间产物 `analysis_ir.json` 可离线复核与增量更新。
2. **中间表示（IR）**：discovery → catalog → profile → semantic → correlate → contract 六段式，每段产物独立、可重放、可逐段定位失败。
3. **字段语义接地**：数据字典按表头关键词识别（含 CSV 字典与 Markdown 管道表），把 `coded_as_text` 与易混字段显式标注。
4. **查询路由**：按 FROM/JOIN 引用表路由到唯一数据源，避免多库静默选错；歧义显式报错。
5. **自检脚本**：`selftest.py` 在临时目录构造空目录/坏 JSON/最小 SQLite 三种场景并断言退出码 0 与合法 result.json，不携带正式案例。

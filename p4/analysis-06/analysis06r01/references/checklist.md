# 质量红线与自检清单（复核用）

本文件是 `SKILL.md` 第 5、6 节的展开版，供 Agent 在末段逐项核验、供评审独立复跑。
所有数字必须来自真实执行，禁止伪造运行日志、连接参数、数值或「已通过」结论。

## 1. 四类输入场景的确定性要求

| 场景 | 期望行为 | 退出码 |
|-|-|-|
| 正常输入（DB/CSV + case.json + task.md） | 产出 report/evidence_map/schema/profiles/CSV/figures，status=partial 或 ok | 0 |
| 空输入目录 | status=insufficient_evidence，pending_human 列缺口，不抛异常 | 0 |
| 坏 JSON / 坏编码 | 记入 errors/degradations，继续处理其余材料 | 0 |
| 缺参数 | `--input/--output/--work` 缺省 `/input`、`/app/output`、`/app/work` | 0 |

验证命令（在 skill 根目录执行）：

```bash
python3 -B scripts/main.py --help
python3 -B scripts/main.py --input /input --output /app/output --work /app/work
python3 -B scripts/selftest.py
python3 - <<'PY'
import json
r = json.load(open('/app/output/result.json', encoding='utf-8'))
assert r['status'] in ('ok','partial','insufficient_evidence','input_error')
assert 'sources' in r and 'uncertainty' in r and 'change_log' in r
print('ok', r['status'])
PY
```

## 2. 逐条红线

1. **证据逐字**：`evidence_map.json` 中每条 claim 必须同时含 `locator`（结构化 dict）与 `quote`（材料逐字片段）；`sources` 同样带 `locator`+`quote`。不允许只写结论。
2. **SQL 可复跑**：连接始终只读（`mode=ro` + `PRAGMA query_only=ON`）；只允许 `SELECT/WITH/PRAGMA/EXPLAIN`，`query_log` 每条记录 SQL、行数与摘要。
3. **行数膨胀**：跨表查询必须显式写连接条件；`infer_join_paths` 记录两侧 `COUNT(DISTINCT)` 与基数（one-to-one/one-to-many/many-to-many），JOIN 后行数必须能对应到连接键基数，否则标 `source_conflict`。
4. **编码型字段**：声明类型为 TEXT 但值全为数字的列标记 `coded_as_text`，只在 Python 侧转数值，禁止直接对文本列 SQL AVG。
5. **缺失/重复**：`NULL` 与空白视为缺失，逐列计数；全行重复用 GROUP BY 全列检测并记录组数与涉行数。
6. **口径一致**：统计值由 Python 从抽取值重算（`checks.recomputed=true`）；不一致必须显式说明，不得静默掩盖。
7. **不编造**：没有数据、没有软件、没有执行记录的步骤写 `null`/`pending_human`，不得填占位数值或伪造「已通过」。

## 3. status 判定参考

- `ok`：数据可查、contract 字段已填、无 errors、关键数值可复算。
- `partial`：有可交付结果但存在未覆盖维度、未填 contract 字段、可解释的降级或待人工项。
- `insufficient_evidence`：无 DB/CSV，或任务要求的证据完全缺失。
- `input_error`：case.json 等输入不可解析且无法继续（保留骨架）。

## 4. 交付前最终核对（提交 Skill 本身）

- [ ] `SKILL.md` YAML frontmatter 含 `name`/`description`/`version`，正文 ≤180 行。
- [ ] `scripts/*.py` 仅标准库，`python3 -B` 可运行，路径相对 `scripts/...`。
- [ ] 不包含 `__pycache__`、`*.pyc`、示例答案、预写正式测试产物。
- [ ] 总文件数 ≤14，总体积 ≤2MB；单文本文件 ≤500KB。
- [ ] `selftest.py` 在干净环境通过（空目录、坏 JSON、正常合成输入三种各一次）。

# 质量红线与 SQL 检查规则（复核用）

本文件是 SKILL.md 第 5、6 节的展开版，供 Agent 末段逐项核验、供评审复跑复算。

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
assert r.get('status') in ('ok','partial','insufficient_evidence','input_error')
assert 'sources' in r and 'uncertainty' in r and 'change_log' in r
print('ok', r['status'])
PY
```

## 2. SQL 检查规则（逐条红线）

1. **证据逐字**：`sources` 与 `evidence_map.json` 中每个结论都带 locator（SQL / 文件#表.字段）与逐字 quote，不允许只写结论。
2. **SQL 可复跑**：连接始终只读（SQLite URI `mode=ro` / `PRAGMA query_only=ON`）；脚本只接受 `SELECT/WITH/PRAGMA/EXPLAIN` 开头语句。
3. **行数膨胀**：跨表必须显式 `JOIN ... ON`；GROUP BY 后行数要能对应到连接键基数，否则标 `source_conflict`。
4. **编码型字段**：声明类型为非数值、但值全为数字的列，标记 `coded_as_text` 并 `CAST(x AS REAL)` 后再聚合，禁止直接对文本列 `AVG`。
5. **缺失/重复**：`NULL` 与空白视为缺失，逐列计数；全行重复用 `GROUP BY 全列 HAVING COUNT(*)>1` 检测并记录组数与涉行数。
6. **口径一致**：SQL 聚合与 Python 重算做一次交叉核对（脚本输出 `matches_python`），不一致必须显式说明并写入 `uncertainty`。

### 常用模板（供 Agent 直接改）

```sql
-- 分组均值（编码字段需 CAST）
SELECT group_id, COUNT(*) n, AVG(CAST(x AS REAL)) mean_x
FROM "samples" WHERE x IS NOT NULL AND TRIM(x) <> ''
GROUP BY group_id ORDER BY group_id;

-- 跨表连接（显式 ON，防膨胀）
SELECT s.group_id, COUNT(DISTINCT m.sample_id) n_labeled
FROM "samples" s LEFT JOIN "meta" m ON s.sample_id = m.sample_id
GROUP BY s.group_id ORDER BY s.group_id;

-- 一行一问：标量答案（配合 contract_key 自动回填）
SELECT CAST(AVG(x) AS REAL) FROM "samples" WHERE group_id = 1;
```

## 3. status 判定参考

- `ok`：数据可查、contract 字段全部填写、无 errors、关键数值已复算一致。
- `partial`：有可交付结果但存在未覆盖维度、未填 contract 字段、可解释的降级或待人工项。
- `insufficient_evidence`：无 DB/CSV，或任务要求的证据完全缺失。
- `input_error`：case.json 等输入本身不可解析且无法继续（保留骨架）。

## 4. 交付前最终核对（提交 Skill 本身）

- [ ] `SKILL.md` YAML frontmatter 含 `name`/`description`/`version`，正文 ≤180 行。
- [ ] `scripts/*.py` 仅标准库，`python3 -B` 可运行，路径相对 `scripts/...`。
- [ ] 不包含 `__pycache__`、`*.pyc`、示例答案、预写正式测试产物。
- [ ] 总文件数 ≤14（本包目标 5–6 个），总体积 60–140KB，单文本文件 ≤500KB。
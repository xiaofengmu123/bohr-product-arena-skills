# 契约、确定性与降级规则（stat-analysis-02）

本文定义 Skill 的字段级契约、`plan.json` 结构、确定性机制、证据定位格式与降级规则，并把规则
映射到评分维度。运行契约的**权威来源是 `/input/case.json` 的 `output_contract`**；本文只约定
信封与固定语义，不替代 case 自定义字段。

## 1. result.json 结构（信封）

```text
case_id                         # 原样复制；缺失 "unknown-case"
status                          # ok | partial | insufficient_evidence | input_error
<output_contract 的全部键>       # 逐键回填：缺值 null/[]，不改名、不删未知键
result{}                        # 主结果：plan/cleaning/descriptive/inferential/effects/CI
quality[]                       # 质量检查（七类情形计数与规则）
descriptive[] / inferential[] / effects[]   # 题目常见字段（与 output_contract 对齐）
interpretation{}                # 结果解释与假设条件
sources[]                       # {source_id, path, kind, rel_path}
uncertainty[] / pending_human[] / errors[] / degradations[]
change_log[]                    # {id, time, action, before, after, reason}
determinism{}                   # {input_fingerprint, deterministic_sha256, rng}
runtime{}                       # {started_at, finished_at, duration_seconds, python_version}
```

- `change_log.time` 用确定性步号 `T01..Tnn`，**不用墙钟**，保证核心产物字节可复现。
- `runtime.wall_clock_note` 显式声明：墙钟是唯一刻意不可复现字段，统计结论不含任何时间依赖。

## 2. 判定层 / 计算层分离（本版核心设计）

| 层 | 谁做 | 产物 | 是否确定性 |
|---|---|---|---|
| 判定层 | Agent（读 task.md/case.json/数据字典） | /app/work/plan.json | 人判，记录 decided_by |
| 计算层 | scripts/*.py（仅标准库） | analysis.json / cleaned / figures / result.json | 严格确定性 |

脚本**永不猜单位、永不猜设计**：设计/单位由 Agent 写入 plan.json；脚本只做启发式默认
（`decided_by: "heuristic"`）供 Agent 复核覆盖。任何主观判断都保留 `decided_by` 与理由。

### plan.json 结构

```json
{
  "design": "two_independent | three_independent | multi_group | paired | correlation | univariate",
  "group_column": "class", "value_columns": ["sepal_length"],
  "numeric_columns": [], "categorical_columns": [], "id_columns": [],
  "units": {"sepal_length": "cm", "未知列": "unit_unknown"},
  "reference_group": "setosa", "alpha": 0.05,
  "missing_rule": "listwise_per_test",
  "outlier_rule": "iqr1.5_flag_no_delete",
  "duplicate_rule": "keep_first",
  "decided_by": "agent | heuristic", "notes": "..."
}
```

## 3. 确定性机制（同输入两次产物一致）

1. 脚本不导入 `random`、不依赖墙钟参与计算；`rng` 恒为 `"none"`。
2. JSON 一律 `sort_keys=True` 固定键序；CSV `lineterminator="\n"`；SVG 固定布局坐标。
3. `input_fingerprint` = SHA-256（排序后的 (rel_path, size, sha256) 清单 + case_id）。
4. `deterministic_sha256` = 对 payload 去掉 `runtime`、并把 `determinism.deterministic_sha256`
   置空后求 SHA-256（哈希不自引用）。`scripts/selftest.py` 跑两遍断言其一致。
5. 浮点非有限值（NaN/Inf）序列化前转 `None`，保证 JSON 合法且字节稳定。

## 4. 证据定位格式（每个关键数值）

- `locator`：来源文件路径 + 位置（如 `cleaned/cleaned_data.csv`、`data.csv#row3`）。
- `quote`：材料中的**逐字片段**；计算结果给计算依据（代码行/公式/seed/fingerprint）。
- 两来源不一致：全部记录并标 `source_conflict`，不隐藏。
- 不虚构 DOI/引用/数值；缺失写 `pending_human` 或 `not_reported`。

## 5. 降级规则（诚实优先）

| 场景 | 处置 | status |
|---|---|---|
| 无任何可分析数据文件 | uncertainty 列缺口，不编造 | insufficient_evidence |
| case.json 缺失/坏 JSON | errors + degradations 记录，case_id 取 unknown-case | partial/ok（数据可分析时 ok） |
| 坏编码文件 | 尝试 utf-8-sig→gb18030→latin-1 容错，记 degradations | 按可用性 |
| 组内方差为 0 / n<2 / 缺失过高 | 该检验标"不适用"，不给 p，说明影响 | partial |
| 单位不明 | 标 unit_unknown，不猜 μg/mg | ok（已注明） |
| scipy 不可用 | 标准库近似（Shapiro-Francia W'、正态近似 p），标口径 | ok |
| SQLite 连接失败 | errors 记录，转文本导出物 | 按可用性 |
| 脚本不可运行 | 手写合法 result.json，退出码恒 0 | partial |

## 6. 评分维度映射

| 评分维度 | 本包对应物 |
|---|---|
| 实际效果·可核验性 | input_fingerprint + deterministic_sha256 + selftest.py + analysis_code.py |
| 使用增益·质量 | 判定/计算分离 + 检验选择表 + 七类情形自动计数 |
| 创新增益·新增能力 | 按分布/设计自动选检验 + 效应量/CI 绑定结论 + unit_unknown 主动追问 |
| 可靠体验·异常处理 | 降级表 + 空输入/坏 JSON/方差为 0/库失败处置 + 退出码约定 |
| 生态贡献·可复用 | 纯标准库、相对路径、plan.json 可独立编辑、两份 reference 文档 |

## 7. 交付前检查

```bash
cd /skills/weekly-skill
python3 scripts/main.py --help          # 退出码 0
python3 scripts/selftest.py             # 退出码 0，25 项检查全过
```

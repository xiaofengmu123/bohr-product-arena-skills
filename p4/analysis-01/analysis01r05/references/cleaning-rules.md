# 字段级清洗规则与决策表（references）

本文件是 SKILL.md 第 5 节的展开：给出每类处理的可复核判据、默认动作与证据要求。默认动作都是保守、可逆的；任何偏离都要在 `result.json.change_log` 里记录 `step/action/why` 与 `locator + quote`。

## 1. 缺失值四分类（互斥、不插补）

| 类别 | 判定（默认） | 例句 | 动作 |
|---|---|---|---|
| `entry_missing` 录入缺失 | 空串、`NA`、`-999`、`.`、`--`、`#N/A` 等哨兵 | `-999`、空 | 保留原文；在 `missing_flags.csv` 标 `entry_missing`；不填均值/中位数 |
| `not_detected` 未检测 | `nd`、`n.d.`、`未检出`、`not detected` | `nd` | 保留原文；分类为 `not_detected`；不是 0、不是缺失 |
| `below_lod` 低于检出限 | 以 `<`/`≤` 开头，或含 `below LOD`、`低于检出限`、`BQL` | `<0.01` | 保留原文；左删失，不进数值统计的 min/max |
| `true_zero` 真值 0 | `0`、`0.0`、`-0` 且字段语义允许 0 | `0` | 保留原文；与缺失、LOD 严格区分 |

- 四类由 `scripts/core_impl.py::classify_value` 做确定性初判，Agent 可依字段语义逐字段覆盖，但必须在 change_log 写明字段、原值、新分类、依据（locator + quote）。
- 禁止"按列均值填补"：这是无依据的总体统计推断，会污染下游统计。

## 2. 异常值处置

判据优先级（由强到弱）：

1. 数据字典 + QC 规则给出的物理上下界/量纲范围（最强，必须写 locator）。
2. 字段语义的物理可行性（如浓度不能为负、比例不能 >1）。
3. 分布诊断仅作为"提示真实高值保留"的参考，不作删除依据。

动作三选一并写理由：

- **保留**：真实高值（如高污染点）且可追溯加样/批次来源；
- **剔除**：可证明是录入错误/加样失误（有对照或批次记录）；
- **盖帽/标记**：在规则明确给上下界时，把界外值标为异常并说明，不改原值。

## 3. 重复记录识别

- **真实主键优先**：样品编号、时间戳、受试者标识、批次号；由字典/工单确认，不得用整行相等当主键。
- **精确重复**：整行逐格相同 → 由脚本分组；保留策略（首条/信息最全/来源最权威）写理由。
- **近似重复**：大小写、空白、全半角、单位写法（`mg/L` vs `mg·L⁻¹`）不一致 → NFKC 归一化后作为**候选**，由 Agent 判断是否同一实体。
- 每组合并/保留/丢弃都必须记录：组内行号、判定键、保留哪条、依据 locator。

## 4. 格式与单位转换

- **编码**：先探测后解析，容错顺序 `utf-8-sig → utf-8 → gb18030 → cp1252 → latin-1`；任何降级写入 `degradations`。
- **数值**：科学计数法、千分位、前导零、尾随零不静默截断；`cleaned_data.csv` 只做空白规整与编码修正，不改数值。
- **单位**：保留 `原始单位 → 目标单位 → 换算因子` 映射表；量纲一致的换算（mg/L↔μg/mL 等）记录因子并重算；**需摩尔质量的换算不猜**，标 `pending_human`。
- **日期/时区**：转 ISO 或保留原文+时区说明，不把日期静默变文本。

## 5. 数据质量报告（数字必须可重算）

- 字段级清单：缺失率、四类计数、异常条数、重复组数、转换记录、处理动作与影响范围。
- 报告所有数字由 `cleaned_data.csv` / `data_profile.json` 实时重算；**先跑 `--phase full` 生成骨架数字，再追加判断**，禁止手写数字。
- 交付时 `--phase selfcheck` 会核对 `result.json` 引用的产物路径真实存在。

## 6. 异常场景处置矩阵（可靠体验）

| 场景 | 识别方法 | 处置 | status/字段 |
|---|---|---|---|
| 空表（0 数据行） | `parse_table` 返回 `n_rows=0` 或 `no data rows` | 不崩溃；写最小结果并注明 | `partial` + `errors` |
| 全列缺失 | 逐列计数 `entry_missing` 占比 100% | 报告该列，不插补 | `uncertainty` |
| 编码乱码 | `read_text_robust` 降级到 gb18030/latin-1 | 记 `degradations`，保留原文快照 | `degradations` |
| 单位不明 | 字典/规则无该字段单位 | 不猜换算 | `pending_human` |
| 主键冲突 | 相同主键出现不同取值 | 两条都记，保留策略写理由 | `source_conflict` + change_log |
| 文件损坏 | 读取/解析抛异常 | 捕获、记 `errors`，继续其余材料 | 不崩溃，退出码 0 |
| 权限不足 | `OSError` 读写失败 | 降级写最小合法 result.json | `errors` + `degradations` |

以上场景统一要求：退出码恒为 0，输出合法 `result.json`，问题写进对应字段，绝不静默。

## 7. 交付前逐字段核对清单

对 `cleaned_data.csv` 的每个字段，逐条确认后打勾：

- [ ] 字段语义与量纲来自数据字典（有 locator），不是按列名猜测；
- [ ] 缺失四分类计数之和 = 该列行数（`present + not_detected + below_lod + entry_missing + true_zero` 对得上）；
- [ ] 异常值判断的上下界来自 QC 规则（有 locator），保留/剔除/盖帽理由成文；
- [ ] 重复组按真实主键确定，保留哪一条及依据可查；
- [ ] 单位换算有映射表与换算因子，精度未丢；需摩尔质量的已标 `pending_human`；
- [ ] 该列在 `quality_report.md` 里的数字 = 由 `cleaned_data.csv` 重算的结果（无手写数字）；
- [ ] 该列的关键改动在 `change_log` 有 `before/after/locator/quote`。

## 8. 每次偏离默认规则时，change_log 条目模板

```json
{
  "time": "…",
  "step": "outlier/duplicate/unit/missing/format",
  "action": "做了什么、改了什么字段",
  "why": "依据（规则文件 + 行号）",
  "locator": "材料文件#位置",
  "quote": "逐字原文",
  "before": "原值",
  "after": "新值/新分类"
}
```

无 locator + quote 的改动视为静默改动，禁止。

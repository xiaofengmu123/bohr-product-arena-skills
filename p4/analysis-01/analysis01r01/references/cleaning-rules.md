# 清洗规则与保守默认（sci-data-cleaning）

本文件是 `SKILL.md` 第 5–7 节的可执行细化；所有判定必须逐字段写依据（locator + 逐字 quote），不得只写结论。

## 1. 缺失值四分类（不互相覆盖）

| 类别 | 典型原文 | 判定要点 | 处理 |
|---|---|---|---|
| 录入缺失 | 空串、`NA`、`-999`、`-9999`、`null`、`—` 等哨兵 | 先查数据字典/规则里声明的哨兵，未声明的哨兵只按词表保守识别并标 `pending_human` | 保留原文，分类记录，不插补 |
| 未检测 | `nd`、`not detected`、`未检出`、`未检测` | 与"低于检出限"不同：未检出是定性结果 | 保留原文，分类记录 |
| 低于检出限 | `<0.01`、`<LOD`、`<DL`、`低于检出限` | 左删失，不是缺失也不是 0；确切的 LOD 数值以 QC 规则为准 | 保留原文，分类记录 |
| 真值 0 | `0`、`0.0`、`0.00` | 只有数值 0 才算真值 0，`<LOD` 与 `nd` 不算 | 保留原文，分类记录 |

红线：四类不互相覆盖；默认不插补；确需标记（如新增派生列）必须在 `change_log` 记录动作与影响范围。脚本 `core_impl.classify_value` 提供与上述一致的确定性实现，`--phase selfcheck` 用同一函数重算，保证报告与结果表一致。

## 2. 异常值判定

1. 首选物理上下界：数据字典或单位/QC 规则给出的 `min`/`max`（如浓度不得为负、心率 0–300）。越界判 `below_min`/`above_max`。
2. 脚本的 IQR（Q1−3·IQR, Q3+3·IQR）只作**诊断提示**，标记 `iqr_outlier`，默认 `action=keep`，不作为删除理由。
3. 保留/剔除/截帽的最终处置由 Agent 决定并写入 `qc_bounds[field].action` 与 change_log；保留真实高值要写明物理依据，剔除加样失误要写明对应工单/记录依据。
4. 不用"均值±n 倍标准差"武断删除；所有处置写入 `evidence_map`（kind=`outlier`）并附原始单元格逐字 quote。

## 3. 重复记录识别

1. 真实主键优先：样本编号、时间戳、受试者标识；从数据字典确认，而不是猜列名。写入 `agent_rules.primary_keys`。
2. 近似重复：主键大小写、空格、全半角、单位写法（如 `mg/L` vs `mg/L `）不一致要归一化后归组（`normalize_token`），单独报告。
3. 保留策略明确：默认保留组内首条，Agent 可用 `duplicate_keep` 覆盖并写明依据。
4. 只有整行完全相同的记录才允许"安全删除"；近似重复默认标记不删，避免误删。

## 4. 格式与单位转换

1. 编码先于解析：`utf-8-sig → utf-8 → gb18030 → big5 → latin-1` 依次尝试；BOM/坏编码记录到 `degradations`。
2. 分隔符自动嗅探（`, \t ; |`），引号内分隔符不算分隔；不按扩展名假设。
3. 数值仅用于画像统计，**不回写原值**；科学计数法、千分位、有效数字不静默截断。
4. 单位换算必须保留映射表与因子：原列保留，新增 `__conv_<字段>_<单位>` 派生列；换算记录进 `evidence_map`（kind=`conversion`）。
5. 需摩尔质量或分子量的换算（如 mol/L）**不猜**，标 `pending_human`。

## 5. 质量报告一致性

- 报告数字的唯一来源是 `data_profile.json` 的 `report` 块；`quality_report.md` 由它渲染，禁止手写数字。
- `--phase selfcheck` 从 `cleaned_data.csv` 重算 `n_rows / n_cols / missing_tallies / n_outlier_flags / n_duplicate_rows / n_duplicate_groups` 并逐项比对，不一致即 FAIL。
- 派生列 `__*` 不计入"原始列数"；真值 0 计入 `missing_tallies.true_zero` 但不计为缺失。

## 6. 自检清单（交付前逐条勾选）

- [ ] 已读 `/input/case.json` 与 `/input/task.md`，`output_contract` 全部键回填（`null`/`[]` 不丢键）。
- [ ] 数据字典、单位/QC 规则、工单均打开并按内容执行，未按文件名臆测。
- [ ] 四类缺失分开，无插补、无静默改值。
- [ ] 异常值有物理上下界与 locator，保留/剔除理由成文。
- [ ] 去重基于真实主键，保留策略明确。
- [ ] 单位/编码转换可逆或留映射，精度未丢。
- [ ] 报告数字与 cleaned_data.csv 一致（selfcheck 重算通过）。
- [ ] 每项关键结论有 locator + 逐字 quote；冲突并列标 `source_conflict`。
- [ ] 空表/坏 JSON/乱码/单位不明/主键冲突/损坏/权限不足均诚实记录，退出码 0。
- [ ] 两次运行数据结果一致；partial 标注局限。
- [ ] `python scripts/selftest.py` 全绿；`--phase selfcheck` 通过。

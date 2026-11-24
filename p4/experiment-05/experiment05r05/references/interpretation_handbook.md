# 判读手册：趋势 / 异常 / 原因 / 复核 / 补做（可复用规则与模板）

本手册给出可直接执行、可复算的判读规则。阈值选择必须**写明方法+数值**，单位/方法/阈值歧义保留并写入 `uncertainty`。示例值仅为示意，不得当作正式案例答案。

## 1. 趋势判定

- 先判定数据形态：宽表（≥10 个数值列且行数≤2000）按“多条曲线”处理；长表（含 batch/fold/group 列）按“分组比较”处理。
- 单曲线：对 (x, y) 做滚动均值（窗口 = max(3, n//20)）后判方向；分段线性拟合找拐点；记录拟合方式与区间。
- 多曲线：逐条算 summary（max/min/mean/std/last/auc_sum），再用 IQR 找离群曲线；趋势写“多数曲线的共同方向 + 离群曲线占比”。
- 分组：每组的均值/标准差对比写方向，注明对照与重复数。禁止“读数复述”——必须给方向、拐点、幅度、单位。

## 2. 异常判定阈值（三选一并写数值）

- z-score：|z|>3，适用于近正态样本（n≥30）。
- IQR：<Q1−1.5·IQR 或 >Q3+1.5·IQR，适用于偏态或小样本（n≥4）。
- MAD：|x−median|>3·1.4826·MAD，稳健、抗离群污染（n≥5）。
- 曲线数据：对 summary 特征（max/auc_sum）套 IQR，把异常绑定到具体曲线列名（通道/批次维度）。
- 超量程/饱和：min 或 max 重复占比≥5%，或全列同值 → 标 `saturated`，不当作普通离群，写入 `limitations` 与 `uncertainty`。

## 3. 原因排序与排除

对每条原因候选给 `rank`（1=最强）、`confidence`（高/中/低），并填支持/排除证据：

- 支持证据：数值/曲线/日志中指向该原因的逐字片段。
- 排除证据：能排除竞争原因的观察（如“若为设备漂移，则所有通道应同向偏移，但只有通道 7 偏移”）。
- `confound_risk`：相关≠因果（如批次与时间共线时注明）。
- 无排除过程的机理猜测 → `confidence=低`，并写“缺哪步验证”。

## 4. 复核步骤（可执行模板）

每条用 `target` + `expected_if_supported` + `decision_rule` 三元组：

- 回查具体文件/字段/行/sheet：如“回查 `data/batch_12.csv` L34–L40 的 force 列，核对是否与仪器日志的记录时间一致”。
- 核对批次与设备日志：写明日志文件名与关键字段。
- 加做对照或平行重复：写明加哪一组、期望什么、若不满足则结论如何降级。
- 禁止“检查数据质量”“进一步验证”等无对象表述。

## 5. 后续实验建议（对应原因候选）

每条写 `change` + `reason` + `expected_outcome`：

- 补对照 / 加密采样点 / 调参数范围 / 扩重复数，选择与证据缺口直接对应的一项。
- `reason` 引用具体原因候选与缺口，`expected_outcome` 写明“若成立，预期观察到什么”。

## 6. 三类常见输入的画像要点

- **多条原始曲线（如弯曲/应力-应变）**：用宽表画像逐条 summary + IQR 离群曲线；报告曲线数、离群曲线列名、每条的 max/auc_sum；趋势写多数曲线共性。
- **分组/交叉验证计算实验（如多 fold、多 estimator）**：用 group 列画像每组的均值/方差/离群；异常定位到 fold/estimator 组合；原因区分“数据泄漏/超参/随机划分/指标定义”并用排除证据收窄。
- **多 sheet 工作簿 + 论文**：先把各 sheet 导出 CSV 逐个画像；再对 PDF 抽文本，把论文中的关键数值与 sheet 数值对照，不一致标 `source_conflict`；单位/方法/阈值歧义一律保留。

## 7. 可复用模板

**趋势条目**：`{"id":"t1","claim":"...","direction":"增","interval":"x=0..4","control":"batch A","fit_method":"线性","magnitude":"0.1 N/mm","unit":"N/mm","evidence":[{"locator":"...","quote":"..."}]}`

**异常条目**：`{"id":"a1","claim":"...","object":"通道7","observed":50.0,"expected":1.2,"deviation":"+48.8","threshold_method":"IQR","threshold_value":2.1,"exceeds":true,"relation_to_control":"对照均值的 40 倍","evidence":[...]}`

**复核条目**：`{"id":"v1","claim":"...","action":"回查 data/batch_12.csv L34-L40 force 列","target":"data/batch_12.csv#L34-L40","expected_if_supported":"与日志记录时间一致","decision_rule":"不一致则异常归因设备/记录，而非样本","evidence":[...]}`

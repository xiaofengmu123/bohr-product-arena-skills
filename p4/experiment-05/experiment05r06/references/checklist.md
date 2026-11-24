# 检查清单与判读方法速查

本文件是 SKILL.md 自检清单的展开：异常判定阈值方法、趋势拟合方式、降级决策表与交付前检查项。

## 1. 交付前自检清单（逐条勾选）

### 1.1 结构与契约

- [ ] `outputs/SKILL.md` frontmatter 含 `name`/`description`/`version`，正文 ≤180 行
- [ ] `result.json` 可被 `json.load` 解析；`case_id` 非空；`status` ∈ {ok, partial, insufficient_evidence, input_error}
- [ ] `case.json` 的 `output_contract` 全部键已回填，未改名、未删未知键
- [ ] 顶层含 `sources`/`uncertainty`/`change_log`/`pending_human`/`runtime`

### 1.2 五段结论

- [ ] `interpretation` 五段均非空，或如实写空并在 `limitations` 说明原因
- [ ] 每条结论带 `evidence`（`locator`+`quote` 均非空），可回指文件/字段/行
- [ ] 异常点写明 `method`+`threshold` 数值；原因写明支持/排除证据与可检验性
- [ ] 复核步骤含 `target`/`expected_if_supported`/`decision_rule`，无“检查数据质量”式空话

### 1.3 数据质量与诚实性

- [ ] 单位不一致、超量程/饱和、坏编码被识别并写入 `uncertainty`/`degradations`
- [ ] 对照缺失、重复数不足被识别并写入 `limitations`/`pending_human`
- [ ] 缺软件/缺依赖如实标注，不臆造；无法判断写 `not_reported`
- [ ] 未编造评测分数、机理、重复数、DOI 或运行结果

### 1.4 可复现性

- [ ] `scripts/selftest.py` 通过（全管线 + 空输入 + 坏 JSON）
- [ ] 同一输入重复运行结果一致（确定排序、无随机数）
- [ ] 脚本只用 Python 标准库；调用路径为相对路径 `scripts/xxx.py`

## 2. 异常判定阈值方法

| 方法 | 公式/规则 | 适用 | 阈值建议 |
|-|-|-|-|
| z-score | `z = (x − μ) / σ`，`|z| > T` 判异常 | 近似正态、n≥20 | T=3.0（严格）或 2.5 |
| IQR | `x < Q1−k·IQR` 或 `x > Q3+k·IQR` | 偏态、稳健 | k=1.5（常规）/3.0（极端） |
| MAD | `|x − median| > k·1.4826·MAD` | 重尾、多离群 | k=3.5 |
| Grubbs | `G = max|x−μ| / σ` 与临界值比较 | 单离群、小样本 | 查表 α=0.05 |
| 手动 | 依物理量程/重复/对照经验设定 | 已知正常范围 | 写明数值与依据 |

规则：
- 判定必须写明**方法与数值**（如 `z_score, T=3.0`），并在 `uncertainty` 保留方法本身的分歧。
- 同一数据建议用至少两种方法交叉核对（如 z-score 与 MAD），结论标注所用方法。
- 超量程/饱和（如读数=满刻度）≠统计离群，二者分开标注：前者写 `degradations`，后者写 `anomalies`。

## 3. 趋势拟合方式

| 方式 | 说明 | 何时用 |
|-|-|-|
| 线性回归 | 最小二乘斜率+相关系数 r | 判断单调方向与幅度 |
| 滚动均值 | 窗口均值（写明 window） | 平滑噪声、暴露拐点 |
| 手动目测 | 说明区间与观察点 | 曲线形态复杂、无法解析拟合 |

趋势结论必须给**方向**（增/减/稳定/非单调/拐点）、**区间**、**对照**与**拟合方式**；仅复述读数不算趋势。

## 4. 降级决策表

| 场景 | 处置 | 退出码 |
|-|-|-|
| 无数据/空输入 | status=insufficient_evidence，写合法骨架列缺口，不填充示例 | 0 |
| 坏 JSON | 记录 `errors`，从 task.md 回退取 case_id，继续处理 | 0 |
| 坏编码 | 降级 latin-1，写 `degradations`，继续处理 | 0 |
| 单文件 >2MB | 跳过并记录 `degradations` | 0 |
| XLSX/MAT/PDF | 先用平台 pandas/openpyxl/pdftotext 导出 CSV/文本再画像 | 0 |
| 对照缺失/重复不足 | 结论降置信，写 `limitations`+`pending_human` | 0 |
| 缺软件/缺依赖 | 标注 limitations，用标准库兜底 | 0 |
| 脚本失败 | 退出码非 0，stderr 说明；Agent 不得编造结果 | 非 0 |

## 5. 三个固定案例形态的解析提示

以下为运行时已知案例形态（不是答案，是解析注意点）：

- **197 条弯曲曲线 + 原始样本工作簿/MATLAB**：按样本维度对齐多条时序曲线，用 z-score/MAD 在样本维度定位异常曲线；趋势写在“力/位移随采样序”上，注明拟合区间。
- **Iris 5-fold/2-estimator 计算实验**：指标（准确率等）按 fold 与 estimator 分组，报告 mean±std；比较两估计器的方向与显著性判断须谨慎（无检验勿断言显著），fold 间异常用 IQR/MAD。
- **Cefixime 12-sheet 工作簿 + 完整论文**：逐 sheet 定位指标与批次，把工作簿读数与论文结论互相核对，冲突时两条都记录并标 `source_conflict`。

以上形态提示不得替代实际读取：始终以 `/input/` 内真实文件为准。

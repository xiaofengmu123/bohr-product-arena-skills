# 自检清单、确定性规则与数据形态路由

本文件是 `SKILL.md` 第 5、8、9 节的展开规则，供 Agent 运行时引用；不预置任何正式案例答案。

## 一、自检清单（与 `scripts/selftest.py` 检查项一一对应）

| 项 | 检查内容 | 通过标准 |
|---|---|---|
| A | result.json 可解析且顶层字段齐全 | `json.load` 成功；`case_id/status/task_type/analysis_mode/summary/interpretation/experiment_plan/evidence_and_risk/confirmation_items/sources/uncertainty/change_log/errors/degradations/limitations/pending_human/runtime/reproducibility` 全存在 |
| B | 标识与状态合法 | `case_id` 非空；`status`∈{ok,partial,insufficient_evidence,input_error}；`analysis_mode` 合法 |
| C | 五段结构 + 证据 | `interpretation` 五键齐全；trends/anomalies 每条带非空 `evidence[]`（locator+quote 均非空） |
| D | 原因可检验性 | possible_causes 每条含非空 `supporting_evidence[]`、`excluding_evidence[]`（可为空）、`confound_risk`、`testability` |
| E | 可执行动作 | verification_steps 每条含非空 `target`；followup_experiments 每条含非空 `reason`+`expected_outcome` |
| F | 防虚构来源 | 每条 locator 中文件路径在输入目录存在（`selftest.py` 自动核验） |
| G | 确定性画像 | 同一输入 `inspect_data.py` 两次运行 `profile_fingerprint` 逐字节一致 |
| H | 内容指纹自洽 | 重新计算的 `content_fingerprint` 与 `reproducibility.content_fingerprint` 一致 |
| I | frontmatter | SKILL.md 含 `name`/`description`/`version` 三个 YAML 键 |
| J | 无编造评分 | result.json 不含 `benchmark_score`/`final_score` 等自报评分字段 |

运行 `python scripts/selftest.py --input /input --result /app/output/result.json`；全部 PASS 返回 0，任一 FAIL 返回 1。

## 二、确定性规则

1. 所有脚本只用标准库，**不 import random、不调用 set、不依赖文件系统遍历顺序**；遍历时 `dirnames.sort()` + `sorted(filenames)`。
2. JSON 写出统一 `ensure_ascii=False, indent=2, sort_keys=True`；浮点用 `round(x, 12)` 规整，去除浮点噪声。
3. 列表规范化：字符串列表按字典序排序；字典列表按 `json.dumps(sort_keys=True)` 稳定排序，避免 Agent 编辑顺序影响产物。
4. 数值结论一律从 `data_profile.json` 或证据 quote 逐字取数，不心算、不重写小数位。
5. **确定性范围**：科学内容（五段结论、方案、证据、画像、内容指纹）两次运行逐字节一致；`runtime` 时间戳与 `change_log` 时间戳是过程元数据，`content_fingerprint` 计算时剔除二者。
6. `main.py finalize` 写 `reproducibility.content_fingerprint`；`selftest.py` 通过"画像复跑两次"与"指纹自洽"两项验证确定性。

## 三、数据形态路由（依据画像 `analysis_mode`，不按文件名臆断）

| analysis_mode | 判据（画像给出） | 解释侧重 |
|---|---|---|
| `curve_trace` | 多个数值表格 + MATLAB 工件，或多列数值时序 | 按 trace/通道维度定位异常，趋势按区间与拟合方式判定，异常绑定具体 trace 编号 |
| `tabular` | 单一规则表格 | 按列维度做离群判定（z/IQR/MAD），对照列与重复列分别标注 |
| `ml_validation` | 评估指标表格（split/estimator 字段） | 比较不同 split 与估计器的均值/方差，报告方差来源，避免把随机波动当真实差异 |
| `workbook_paper` | 多页 XLSX + PDF 论文 | 工作簿先导出 CSV，论文提供机制与历史依据，两来源冲突时两条都记并标 `source_conflict` |
| `mixed` / `unknown` | 无法单一归类 | 如实降置信，写明不确定来源 |

路由只决定"解释侧重点"，不改变输出契约；任何结论仍必须回指 locator+quote。

## 四、证据书写规范

- `locator` 至少含文件路径，尽量到行/段/列：`data/batch_12.csv#L34`、`materials/paper.md#p3`、`notes.xlsx#Sheet1!B12`（工作簿导出后改用 CSV 行号）。
- `quote` 逐字摘录原文，含原始单位、符号与大小写；无法逐字则写 `not_reported` 并进 `pending_human`。
- 同一指标两来源冲突：两条都记录，且至少一条标注 `source_conflict: true`。
- 阈值必须写方法+数值（如 `z_score:3.0`、`IQR:1.5`、`MAD:3.5`），不写"明显异常"这类无阈值表述。

## 五、降级与诚实性

- 材料不足 → `status=insufficient_evidence`，`limitations` 列出具体缺口，不填示例内容。
- 坏编码/坏 JSON/超量程/饱和/单位不一致 → 记 `degradations`/`errors`/`uncertainty`，保留原始值，不做无依据换算。
- 对照缺失、重复数不足、机制无排除证据 → 结论降置信，写 `limitations` 与 `pending_human`。
- 缺软件 → 如实写 limitations；脚本仅标准库，XLSX/MAT/PDF 依赖平台工具时标记 `convert_first`。

# 自检清单与异常处置

本清单对应评分维度：实际效果(40)、使用增益(25)、创新增益(15)、可靠体验(10)、生态贡献(10)。逐条勾选，结论写入 `result.json` 的 `contract_alignment` / `uncertainty` / `change_log`。

## A. 输出契约完整（本变体重点）
- [ ] `contract_fields` 是契约叶子的完整路径清单；`contract_alignment` 与之长度一致。
- [ ] 每个 `contract_alignment` 项含 `path/leaf_key/raw_value/resolved_kind/fill_status/value/reason/change_log_index`。
- [ ] 每个 `fill_status=null` 的字段都进入 `contract_declined`，且 `change_log` 有对应 `S5_contract_align` 记录。
- [ ] 保留键冲突（如契约含 `sources`/`status`）只在 `output_contract` 内保留，不平铺顶层，并在 `degradations` 说明。
- [ ] 契约缺失时 `contract_source=default`，使用三类交付字段兜底并在 `uncertainty` 说明。

## B. 科学实体
- [ ] 基因名（斜体、大小写，如 TP53）、蛋白名、物种拉丁名、化合物/药物名与源材料逐字一致。
- [ ] 中文专名无乱码/方框；单位符号（µ、μ、°、×、±、⁻¹、kDa、mg/mL、ng/mL）正确。
- [ ] 缩写首次出现给出全称，图注与正文引用一致。

## C. 空间关系（示意图/机制图/流程图）
- [ ] 实体间箭头方向、抑制/激活关系、上下游顺序与源材料一致。
- [ ] 多面板对齐、子图编号（a,b,c）与正文引用一致；图层顺序无遮挡。

## D. 数据编码（数值→图元）
- [ ] 每个柱高/点位置/色阶/扇区角度/误差线长度能回溯到源数据列与行号（`data-source`/`data-row`/`data-col`/`data-value`）。
- [ ] 量级与单位（ng/mL vs µg/mL、% 与小数、10³ vs 10⁶）核对无误。
- [ ] 坐标轴刻度、范围、线性/对数刻度选择正确。

## E. 图例与标注
- [ ] 图例系列与数据系列一一对应，无错配、无遗漏。
- [ ] 误差线含义（SD/SEM/CI/n）与显著性标记（如 * p<0.05）有图注说明；样本量一致。
- [ ] 轴标签含单位；标注无遮挡、无超长截断。

## F. 可编辑源文件与导出
- [ ] 分层 SVG（文字 `<text>`，未转曲）或 PPTX/EPS 可继续编辑。
- [ ] 源文件与导出件同一数据、同一版本；重跑脚本可复现同一张图。
- [ ] PNG 分辨率≥300 dpi（或按目标要求）、颜色模式正确、无意外透明背景；PDF 矢量文字可选中、字体嵌入。

## 异常处置（可靠体验）
| 异常 | 处置 |
|---|---|
| 缺列/空值 | extract 已报 missing；核对源文件，仍缺→契约字段 `null` + `contract_declined` + `uncertainty` |
| 量级异常 | 报 issue，人工核对单位；不确定则保留原值并标注 |
| 标签过长 | 缩写 + 图注全称，或旋转/换行 |
| 字体缺失 | font-discovery 查中文字体；无则英文标签或注明乱码风险 |
| 导出失败 | 标准库手写 SVG 兜底；PNG/PDF 无法渲染→字段 `null` 且 `partial` |
| 标注遮挡 | 调整面板尺寸/图例位置/留白后重渲 |
| 契约键无法解析 | `resolved_kind=unknown`，填 `null` 并 `pending_human` |

## 最小可复核结果（降级时仍须满足）
1. `/app/output/result.json` 存在且含 `case_id`、`status`、`sources`、`uncertainty`、`change_log`。
2. 每个契约字段要么有产物/对象，要么 `null` 且进 `contract_declined` + `change_log`。
3. 至少一个可编辑 SVG + 一份来源与修改说明。

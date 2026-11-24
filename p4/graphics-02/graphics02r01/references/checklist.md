# 自检清单与异常处置

本清单对应评分维度：实际效果(40)、使用增益(25)、创新增益(15)、可靠体验(10)、生态贡献(10)。逐条勾选，并把结论写入 `result.json` 的 `change_log`、`uncertainty` 或 `pending_human`。检查由 `scripts/selftest.py` 自动执行，人工/Agent 复核负责语义层判断。

## A. 科学实体（命名正确性）
- [ ] 基因名（斜体、大小写，如 TP53）、蛋白名、物种拉丁名、化合物/药物名与源材料逐字一致。
- [ ] 中文专名无乱码/方框；单位符号（µ、μ、°、×、±、⁻¹、kDa、mg/mL、ng/mL）正确。
- [ ] 缩写首次出现给出全称，图注与正文引用一致。
- [ ] 实体名有 evidence_map 记录：`element_type=text/node`，`quote` 为源材料原文。

## B. 空间关系（示意图/机制图/流程图）
- [ ] 实体之间箭头方向、抑制/激活关系、上下游顺序与源材料一致。
- [ ] 多面板对齐、子图编号（a, b, c）与正文引用一致；图层顺序无遮挡。
- [ ] 每条边/箭头有 evidence_map 记录：`element_type=edge`，`mapping` 写明“A 抑制 B / A 激活 B”。

## C. 数据编码（数值→图元）
- [ ] 每个柱高/点位置/色阶/扇区角度/误差线长度能回溯到源数据的列与行号。
- [ ] 量级与单位（ng/mL vs µg/mL、% 与小数、10³ vs 10⁶）核对无误。
- [ ] 坐标轴刻度、范围、线性/对数刻度选择正确。
- [ ] 每个数值图元在 SVG 中带 `data-source`/`data-locator`/`data-element` 属性，与 evidence_map 一致。

## D. 图例与标注
- [ ] 图例系列与数据系列一一对应，无错配、无遗漏。
- [ ] 误差线含义（SD/SEM/CI/n）与显著性标记（如 * p<0.05）有图注说明。
- [ ] 样本量 n、重复数在图注或面板上可见且与源数据一致。
- [ ] 轴标签含单位；标注无遮挡、无超长截断。

## E. 可编辑源文件
- [ ] 分层 SVG（文字为 `<text>`，未转曲）或 PPTX/EPS 可继续编辑。
- [ ] 源文件与导出件一致（同一数据、同一版本）；重跑脚本能复现同一张图。
- [ ] SVG 根节点含版本/来源/生成时间元数据（`data-version`、`data-sources`）。

## F. 导出格式
- [ ] PNG 分辨率≥300 dpi（或按目标要求），颜色模式正确，无意外透明背景。
- [ ] PDF 矢量文字可选中、字体嵌入；尺寸与留白符合目标期刊或模板。
- [ ] 若渲染器缺失，PNG/PDF 字段为 `null` 且 uncertainty 写明，不伪造导出件。

## G. 证据溯源（本版强化）
- [ ] `evidence_map` 非空；每条含 `element_id`/`locator`/`quote`/`source_file`。
- [ ] `quote` 是源材料逐字子串（selftest 校验，找不到→`quote_miss`）。
- [ ] 关键数值的 `value` 与源材料一致；`mapping` 写清编码规则。
- [ ] 冲突信息不隐藏：两处来源不一致时两条都记录并标 `source_conflict`。

## 异常处置（对应可靠体验 10 分）
| 异常 | 处置 |
|---|---|
| 缺列/空值 | `extract` 已报 missing；核对源文件，仍缺→`null` + `uncertainty` |
| 量级异常 | 报 issue，人工核对单位；不确定则保留原值并标注 |
| 标签过长 | 缩写 + 图注全称，或旋转/换行标签 |
| 字体缺失 | font-discovery 查找中文字体；无则英文标签或注明乱码风险 |
| 导出失败 | 换标准库手写 SVG 兜底；PNG/PDF 无法渲染则如实 `partial` |
| 标注遮挡 | 调整面板尺寸/图例位置/留白后重渲 |
| quote 检索不到 | 核对 locator 是否指向解析文本（非原始 PDF 二进制）；仍缺→`quote_miss` + 人工复核 |
| 源材料超 2MB | 跳过并记录到 `sources`/`uncertainty`，不硬读 |

## 最小可复核结果（降级时仍须满足）
1. `/app/output/result.json` 存在且含 case_id、status、sources、uncertainty、pending_human、change_log、evidence_map。
2. 每个 contract 字段要么有产物/对象，要么 `null` 且注明原因。
3. 至少一个可编辑 SVG（或手写 SVG 骨架）+ 一份来源与修改说明。

## 重跑一致性（可复核性 5 分）
- [ ] 固定随机种子；SVG 布局由 spec 参数唯一确定（同输入→同输出）。
- [ ] `sources` 记录材料版本（文件名+哈希）与脚本版本（version）。
- [ ] 第三方可仅凭 `/input` + SKILL.md + scripts 重跑得到同一张图。

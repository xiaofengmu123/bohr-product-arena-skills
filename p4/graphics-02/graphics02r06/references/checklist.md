# 自检清单与异常处置

本清单对应评分维度（实际效果 40 / 使用增益 25 / 创新增益 15 / 可靠体验 10 / 生态贡献 10）。
逐条勾选，结论写入 `result.json` 的 `change_log` / `uncertainty`，最终由
`python scripts/selftest.py` 生成 `/app/work/check_report.json`。

## A. 科学实体（命名正确性）
- [ ] 基因名（斜体、大小写如 TP53）、蛋白名、物种拉丁名、化合物/药物名与源材料逐字一致。
- [ ] 中文专名无乱码/方框；单位符号（µ、μ、°、×、±、⁻¹、kDa、mg/mL、ng/mL）正确。
- [ ] 缩写首次出现给全称，图注与正文引用一致。

## B. 空间关系（示意图/机制图/流程图）
- [ ] 实体间箭头方向、抑制/激活关系、上下游顺序与源材料一致（写入 `plan.relationships`）。
- [ ] 多面板对齐、子图编号（a, b, c）与正文引用一致；图层顺序无遮挡。

## C. 数据编码（数值 → 图元）
- [ ] 每个柱高/点位置/色阶/误差线长度可回溯到 `source.locator` 的列与行号。
- [ ] 量级与单位（ng/mL vs µg/mL、% 与小数、10³ vs 10⁶）核对无误。
- [ ] 坐标轴范围、线性/对数刻度选择正确（由 `figure_model.axes` 显式声明，不靠默认）。

## D. 图例与标注
- [ ] 图例系列与数据系列一一对应，无错配、无遗漏。
- [ ] 误差线含义（SD/SEM/CI/n）与显著性标记有图注说明（`caption`）。
- [ ] 样本量 n、重复数在图注或面板可见且与源数据一致。
- [ ] 轴标签含单位；标注无遮挡、无超长截断。

## E. 可编辑源文件
- [ ] SVG 文字为 `<text>` 节点（未转曲）；`selftest` 校验 `svg_editable` 通过。
- [ ] 源文件与导出件同数据同版本；重跑 `render.py` 能复现同一张图（场景确定性）。

## F. 导出格式
- [ ] PNG ≥300 dpi、RGB、无意外透明背景（`manifest.json` 记录 dpi/色彩空间/背景）。
- [ ] PDF 矢量文字可选中、字体嵌入；尺寸与留白符合目标场景。

## 异常处置（对应可靠体验）

| 异常 | 处置 |
|---|---|
| 缺列/空值 | 在 `data_model.cells[].issue` 记录；仍缺 → 契约字段 `null` + `uncertainty` |
| 量级异常 | 记录 issue，人工核对单位；不确定则保留原值并标注，不擅改数 |
| 标签过长 | 缩写 + 图注全称，或旋转/换行标签 |
| 字体缺失 | `font-discovery` 查中文字体；无 → PNG 改英文标签或 `uncertainty` 注明乱码风险 |
| 无 matplotlib | `render.py` 只出 SVG 并在 `manifest.issues` 记录；PNG/PDF 为 `null`，不伪造 |
| 标注遮挡 | 调 `canvas`/`bbox`/图例位置后重渲 |
| 同一策略两次失败 | 停止重复，换策略或降级为“最小可复核结果” |

## 最小可复核结果（降级时仍须满足）
1. `/app/output/result.json` 存在，含 `case_id`、`status`、`sources`、`uncertainty`、`change_log`。
2. 每个契约字段要么有产物/对象，要么 `null` 且 `uncertainty` 注明原因。
3. 至少一个可编辑 SVG + 一份来源与修改说明（`sources` / `change_log`）。

## 与参考打法的差异（本版设计取舍）
- 参考方案以“extract_tables → 直接 matplotlib 出图 → check”为主线；本版改为
  **“LLM 提炼四层中间表示 → 确定性脚本渲染 → 双向溯源校验”**，渲染与 LLM 解耦。
- 由此获得的实质改进：同一输入可复现、图元与源数据双向可定位、图面判断不依赖渲染时刻的 LLM 状态。
- 代价：LLM 需多填一层结构化 JSON；由 `model_spec.md` 与脚本的容错校验兜底。

# 自检清单（对应评分维度，逐条勾选）

提交/交付前逐条勾选；未满足项写入 `result.json.uncertainty`，**不假装通过**。
自动可测的项标注 [auto]，只能人工核验的标注 [manual]。

## 实际效果（40）
- [ ] [auto] 模板被真实套用：`render_record.integrity.pptx` 显示 masters/layouts>0，
      每页 `slide_layouts[].layout` 非空、`placeholder_inherited` 为 true（或已如实记 E6/E5）。
- [ ] [manual] 页内文字、数字、图号与源材料**逐字一致**；每个关键结论有 `locator`+`quote`。
- [ ] [manual] 覆盖封面→正文→结论主要页型，六类要素（主线/页结构/图引用/讲解逻辑/模板约束/导出渲染）齐全。
- [ ] [auto] 交付可打开、可继续编辑的 pptx（zip 可解、`[Content_Types].xml` 与 `ppt/presentation.xml` 存在），
      以及真实渲染的 PDF（`%PDF-` 头）与 PNG（PNG magic）。
- [ ] [auto] `sources` / `template_file` / `exports` / `change_log` 齐全，他人可复跑逐页核对。

## 使用增益（25）
- [ ] [manual] 同一输入下先落骨架、后增量改进；`result.json` 首条 `change_log.action=="init"`。
- [ ] [manual] `runtime` 记录实际耗时/请求数/改动页数；对照用事实，不用自述代替证据。

## 创新增益（15）
- [ ] [manual] 明确选择本版突破方向：**不确定性显式化 + 缺数据不编造**。
- [ ] [manual] 字号与间距阶梯、图表等比缩放、批量一致性规则可解释并写入 SKILL.md。
- [ ] [auto] `validate` 输出 `auto_checkable` 边界：哪些是脚本确定性可测（zip/占位符/导出头），
      哪些只能人工核（真实排版美观、图号语义、数据对应）。

## 可靠体验（10）
- [ ] [manual] 输入输出路径、调用顺序、900s/64 请求预算写清；他人可按说明复现"论文+模板→pptx+PDF"。
- [ ] [auto] 空输入/坏 JSON/缺输入/坏 pptx/非 PDF 均能降级不崩溃（`scripts/selftest.py` 全 PASS）。
- [ ] [auto] 重复运行幂等：`init` 落骨架合并而非覆盖，`validate`/`evidence` 用 `append_unique` 去重。

## 生态贡献（10）
- [ ] [manual] 模板准备规范、参数含义、导出设置、字体要求、限制与维护信息齐全。
- [ ] [manual] 脚本只用标准库、路径相对可执行；`core_impl.py` 的检查函数可被直接复用。
- [ ] [manual] 沉淀规则清单（本文件 + `output_contract.md`），不夹带预写答案。

## 可解释排版规则（默认阶梯，按模板主题色/主题字体覆盖）

这是制作阶段必须遵守、且他人可复核的规则；不满足时写进 `uncertainty`，不静默改值。

**字号阶梯（px/pt 按 16:9 版心估算，模板主题优先）**
- 页标题 28–32，节标题 20–24，正文 16–18，注释/图注 11–12，页脚 10。
- 单页正文 ≤ 5 条要点，每条 ≤ 2 行；超过即拆页或降层级，不靠无限缩小字号硬塞。

**间距阶梯**
- 标题上 0.4em / 下 0.2em，要点间距 0.3em，段内行距 1.15–1.3，页边距 ≥ 版心 6%。
- 同类元素（标题位置、留白、页脚、页码）跨页对齐一致；批量改后跑 `validate` 复核。

**图表等比缩放**
- 容器内 `contain` 等比缩放，绝不 `stretch` 变形；矢量图（svg/emf）优先，位图放大倍数 ≤1.5 且保留轴/图例可读。
- 图号统一编号 `图1/Fig.1`，正文引用与图号一致；不一致记 `E8_MEDIA_BROKEN` 或 `E12_MANUAL_REVIEW`。

**字体颜色**
- 中英文字体搭配、主题色 + 单一强调色；正文对比度 ≥ 4.5:1；字体缺失用 font-discovery 查回退并记 `E7_FONT_MISSING`。

## 不确定性代码清单（uncertainty 使用）

| 代码 | 含义 |
|---|---|
| E1_INPUT_MISSING | 必需输入文件/目录缺失 |
| E2_JSON_MALFORMED | case.json 无法解析 |
| E3_ENCODING_ISSUE | 文本编码异常（已降级读取） |
| E4_OVERSIZE_SKIPPED | 材料文件超 2MB，跳过并记录 |
| E5_TEMPLATE_NO_LAYOUT | 模板缺所需版式，使用替代版式 |
| E6_PLACEHOLDER_MISSING | 占位符缺失或 idx 未在版式定义 |
| E7_FONT_MISSING | 目标字体不可用，记录回退字体 |
| E8_MEDIA_BROKEN | 图表源文件缺失/损坏 |
| E9_EXPORT_FAILED | PDF/PNG 导出失败，预览缺失 |
| E10_QUOTE_NOT_LOCATED | 证据定位失败，不能确认逐字一致 |
| E11_SOURCE_CONFLICT | 多来源对同一指标不一致 |
| E12_MANUAL_REVIEW | 只能人工核验 |
| E13_HEURISTIC_ONLY | 仅启发式检查，非真实渲染判定 |

## 一键自检

```bash
python scripts/main.py --help          # 退出码 0
python scripts/selftest.py             # 退出码 0，全部 PASS
python scripts/main.py init --input /input --output /app/output --work /app/work
python scripts/main.py validate --pptx /app/output/deck.pptx --pdf /app/output/deck.pdf \
    --png /app/output/slide-*.png --result /app/output/result.json
python scripts/main.py evidence --plan /app/work/page_plan.json --texts /app/work/extracted \
    --result /app/output/result.json
python scripts/main.py seal --result /app/output/result.json
```

# 自检清单与降级矩阵

每页产出后逐条勾选；未满足项写入 `result.json` 的 `uncertainty`/`exceptions`，不假装通过。脚本化自检见 `scripts/selftest.py`。

## 实际效果（40）

- [ ] 源论文/材料被真正写入模板，而非整段粘进默认模板；母版/版式/主题继承关系可在 pptx XML 查到（`audit` 的 `template_inheritance`/`layout_ref`）。
- [ ] 页内文字、数字、图号与源材料一致；图表与数据对应，图号与正文引用一致（`evidence` 逐字命中）。
- [ ] 覆盖封面→正文→结论的主要页类型，六类要素（主线/页结构/图引用/讲解逻辑/模板约束/导出渲染）齐全（`role_coverage`）。
- [ ] 交付可打开、可继续编辑的 pptx，以及真实渲染的 PDF/PNG 预览（`editable_source`/`export_artifact`/`png_preview`）。
- [ ] 源文件、模板、导出件与逐页偏差记录齐全，他人可复跑逐页核对（`report.md` + `evidence_map.json` + `template_map.json`）。

## 使用增益（25）

- [ ] 同一输入下先 `init` 落骨架、后增量改进，减少返工页数；每页 `notes` 即讲稿，降低人工核对量。
- [ ] `runtime` 记录实际耗时/步骤数，`change_log` 记改动页；不用自述代替证据。

## 创新增益（15）

- [ ] 明确选择突破方向（模板套用/版式约束/字体颜色/图表适配/批量一致性之一），本包为「IR 单一事实源 + round-trip 审计」。
- [ ] 字号与间距阶梯、图表等比缩放策略、批量一致性校验有可解释规则（标题 28pt/正文 18pt/页码 10pt，主题字体 `+mj-lt/+mn-lt`，主题色 `schemeClr`，等比 `contain`）。
- [ ] 用 `audit` 的确定性检查证明方法边界（pptx 结构/页数/继承/rel 引用/round-trip 可自动测；溢出与字体回退效果只能人工核）。

## 可靠体验（10）

- [ ] 输入输出路径、调用顺序、时间预算写清；他人能按说明复现「论文+模板→pptx+PDF/PNG」。
- [ ] 缺版式、占位符缺失、文本框溢出、字体缺失、图表失效、导出失败均有识别与处置路径（见降级矩阵）。
- [ ] 重复运行幂等：`init` 合并而非覆盖、zip 固定时间戳/parts 排序、`compile` 两次 pptx 字节一致、脚本退出码语义明确（异常仍退出 0 且写 `input_error`）。

## 生态贡献（10）

- [ ] 模板准备规范、参数含义、导出设置、字体要求、限制与维护信息齐全（`SKILL.md` + `references/contract.md`）。
- [ ] 脚本只用标准库、路径相对可执行，模板适配层与检查脚本可被直接复用。
- [ ] 沉淀了可分享的规则/清单（本文件 + contract.md），不夹带预写答案。

## 降级矩阵（诚实 > 完整）

| 场景 | 降级行为 | 交付状态 | 人工接管 |
|---|---|---|---|
| `case.json` 缺失/坏 JSON | 仍写合法 result.json，错误进 `errors` | `input_error` | 查看 errors 并补输入 |
| 材料无文本层/不可解析 | 标注 `insufficient_evidence`/`pending_human` | `partial`/`insufficient_evidence` | 用 OCR/渲染工具抽取后重跑 |
| 证据引文未逐字命中 | lint 报 WARN，quote_status=paraphrase | `partial` | 逐字回原文修正 quote |
| 模板缺所需 layout | 按角色选最接近版式，替代记入 `template_map.json.substitutions` | `ok`/`partial` | 确认替代可接受 |
| 无可用模板 | 回退内置母版生成真实 pptx，继承检查记 fail | `partial` | 补模板后重跑 compile |
| 字体缺失 | 用主题字体回退，记 `exceptions` | `partial` | font-discovery 确认可用字体 |
| 图表源失效 | 保留 null 路径 + 说明，不伪造图片 | `partial` | 补图源后重跑 |
| 导出失败 | 保留脚本兜底 PDF/PNG，engine 字段如实标注 | `partial` | office-renderer 重导 |
| 输出目录不可写 | 兜底到 `<work>/output_fallback` 并记降级 | 按实情 | 检查挂载权限 |
| 任何未知异常 | 顶层捕获写 `input_error`，退出码恒 0 | `input_error` | 查看 errors 与 report |

## 诚实性边界

- 不把"未报告"写成"不存在"；不把推断写成原文直读；不伪造运行日志、引文、继承或分数。
- 无法确定的数值/单位/结构写 `uncertainty`/`pending_human`，并说明需要什么才能确认。

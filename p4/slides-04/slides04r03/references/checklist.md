# 自检清单与可解释排版规则

本文件把 SKILL.md §5 的清单展开为“逐条可勾选 + 判定标准 + 处置动作”，并把字号间距阶梯、图表缩放策略写成可解释规则。完成状态标记：`[x] 通过`、`[!] 部分（写 uncertainty）`、`[ ] 未做（写 uncertainty）`。

## A. 字号与间距阶梯（可解释规则）

- 版心基准：以模板母版占位符边界为版心；不把字塞出占位符，也不把字号压到不可读（正文 ≥14pt，图注/脚注 ≥9pt）。
- 层级阶梯：`封面标题 ≥28pt > 节标题 24pt > 页标题 20pt > 正文 16pt > 图注/脚注 10pt`；相邻层级差 ≥4pt，保证可读阶梯。
- 间距阶梯：`标题上下留白 ≥ 0.3×标题字号`，`段间距 ≥ 1.2×行距`，`页面边距 ≥ 0.5in`；同层级跨页统一。
- 中西文字体：中文用模板 `ea` typeface，西文/数字用模板 `latin` typeface；缺字回退用 font-discovery 查到的字体，并写入 `render_record.fonts`。
- 颜色：正文用主题色 `tx1`，强调用主题色 `accent1`，不引入无出处的高饱和色；对比度按 WCAG 近似 4.5:1 目视核对。

## B. 图表等比缩放策略（可解释规则）

- 只做**等比缩放**（等比 `min(scale_w, scale_h)`），不做单向拉伸；矢量图优先，位图放大不超过原始像素尺寸 1.5×。
- 图号与正文引用统一：正文引用“图 N”须与图注“图 N”一致；改动后全册重查一次图号引用。
- 坐标轴/图例可读性：缩放后字号换算不低于 9pt；不可读则裁切图例外的留白，不裁掉坐标轴。
- 溢出与裁切：用 `validate_deck.py` 的 `overflow_candidates` 做启发式提示；真实渲染以 office-renderer 导出的 PDF/PNG 为准。

## C. 逐维度自检（对应评分维度）

### 实际效果（40）
- [ ] 模板真实套用：`validate_deck.py` 输出 `layouts_used` 非空且 `layout_name` 可查；母版 rels 存在。
- [ ] 数字/图号一致：每页 `claim` 中的数字在 `quote` 中逐字找到；图号与正文引用一致。
- [ ] 页型齐全：`page_plan[]` 覆盖封面、问题、方法、结果、限制、结论；六类要素全部命中。
- [ ] 可编辑 + 可预览：`pptx_path` 与 `pdf_path`/`png_paths` 均非 null，PNG 可解尺寸。
- [ ] 可复跑核对：`evidence_map` 每条 ref 有 `locator + quote`；`render_record` 记录 source_files/template/layouts/fonts。

### 使用增益（25）
- [ ] 先骨架后增量：`change_log` 含 `skeleton_created` 与后续 `merged` 事件，时间戳为 `--now` 注入值。
- [ ] 减少返工：`input_inventory.json` 记录材料清单；每页 `notes` 即讲稿，人工只核页面不重读全文。
- [ ] 增益可验证：记录实际耗时/请求数/改页数；`selftest.py` 输出可复现的 PASS/FAIL 列表。

### 创新增益（15）
- [ ] 突破方向明确：批量一致性与确定性自检；`selftest.py` 证明同输入两次产物字节一致。
- [ ] 方法可解释：A、B 两节的阶梯与缩放规则在本文件写明，并与脚本实现对应。
- [ ] 创新证据：`validate_deck.py` 的确定性检查（zip/页数/版式/字体/PNG/evidence locator+quote）与只能人工核的项（美观、图号引用、字体真实回退、拉伸）有明确分界。

### 可靠体验（10）
- [ ] 调用清晰：SKILL.md 写明路径、顺序、时间预算；`scripts/<name>.py --help` 可查参数。
- [ ] 异常有处置：缺版式/占位符/溢出/缺字体/图源失效/导出失败均在 §6 有路径。
- [ ] 幂等与确定性：`init_result.py` 合并不覆盖；`selftest.py` 双跑字节一致；exit code 语义见 SKILL.md §7。

### 生态贡献（10）
- [ ] 文档齐全：`output_contract.md` 覆盖字段语义与状态取值；本文件覆盖规则与判定标准。
- [ ] 资源可复用：三个脚本只用标准库、相对路径；模板适配层与检查脚本可直接复用。
- [ ] 沉淀规则：字号/间距/缩放规则 + 清单可分享；无预写答案、无正式测试产物。

## D. 未满足项登记格式

每项未满足写入 `result.json.uncertainty`：

```json
{"code": "layout_missing", "page": 3, "detail": "模板无 section_header 版式，改用 content 并记录替代关系"}
{"code": "font_fallback", "page": null, "detail": "SimSun 缺失，回退 Noto Sans CJK SC"}
{"code": "figure_source_missing", "page": 5, "detail": "fig3.png 源失效，png_path 置 null"}
```

`code` 取 `layout_missing / placeholder_missing / overflow / font_fallback / figure_missing / export_failed / evidence_not_reported / source_conflict` 之一；`detail` 写清证据与影响页。**不假装通过，不编造文件与分数。**

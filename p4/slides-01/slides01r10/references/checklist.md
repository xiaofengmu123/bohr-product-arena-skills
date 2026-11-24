# 自检与降级清单（交付前逐条核对）

本清单与 `scripts/main.py --selftest` 互补：selftest 做机器可判的自动化检查，
下面逐条由 Agent 在交付前人工勾选，每条对应评分维度。

## A. 交付契约（结果可核验性 / 完整性）

- [ ] `/app/output/result.json` 存在，`case_id` 与 `/input/case.json` 一致
- [ ] `status` ∈ `ok | partial | insufficient_evidence | input_error`，且诚实反映状态
- [ ] `output_contract` 的全部字段已原样回填（未知键保留，缺值给 `null`/`[]`/`{}`）
- [ ] `sources` / `uncertainty` / `pending_human` / `change_log` / `errors` / `degradations` / `runtime` 齐全
- [ ] `presentation.pptx` 与 `presentation.pdf` 均真实存在，非占位空文件

## B. 主线与页结构（任务完成度 / 完整性）

- [ ] `spine` 四要素（问题/做法/关键证据/结论）齐全，页序是叙事而非段落顺序
- [ ] 每页一个 `claim`，`role` 合法，要点数量与密度受控
- [ ] 页数 ≤ `meta.slide_cap`，超限已优先保留主线页

## C. 证据与图表（结果正确性 / 可核验性）

- [ ] 每页 `evidence` 有 `source_id + locator + 逐字 quote`，`--verify-quotes` 通过
- [ ] 数值、单位、方法名、结论表述与论文一致，未改写
- [ ] `figure_ref/table_ref` 都能回溯到真实图/表，编号与正文引用一致，无编造、无张冠李戴
- [ ] 示意图未被当作实验数据展示

## D. 讲解与模板（讲解逻辑 / 模板约束）

- [ ] 每页 `notes` 含 transition / emphasis / q_backup，且不重复页面文字
- [ ] pptx 真继承模板 master/layout（`add_slide(layout)` 而非“空白+贴文本框”）
- [ ] 长标题、多图、大表格已在占位符与字号层级内排布

## E. 导出与稳定性（导出渲染 / 运行稳定）

- [ ] `slides` 数 == PDF 页数（`compliance_checks` 已记录）
- [ ] 换机打开字体/图形/项目符号不崩坏（字体缺失已标注）
- [ ] `python3 -B scripts/main.py --selftest` 全 PASS
- [ ] 空目录 / 坏 JSON / 缺输入三种情况各验证一次均退出码 0

## F. 降级与诚实标注（异常处理 / 生态贡献）

- [ ] 缺数据 → `insufficient_evidence` + `uncertainty`/`pending_human`，未编造
- [ ] 缺软件（python-pptx/soffice）→ `exceptions`/`degradations` 如实标注，用 SVG/PNG/PDF 降级
- [ ] PDF 加密 / 公式为图片 / 字体缺失已逐项记录
- [ ] `change_log` 记录了每次增量修改（骨架 → IR → 渲染 → 组装）

> 任何一条勾不上，就在对应字段写明原因；**不写“已通过”的假检查**。

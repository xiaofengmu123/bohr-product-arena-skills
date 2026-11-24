# 运行期自检与查错方法（references，非正式案例）

本清单在 P4/P5 阶段逐条执行；未通过的项要么修复，要么写入 result.json 的
`uncertainty` / `errors` / `degradations`，绝不静默跳过。可用
`python3 scripts/selftest.py` 自动跑包结构与组件自检。

## A. 实际效果（40 分：完成度 / 正确性 / 完整性 / 可核验性）

- [ ] 主产物为可编辑 `.tex`（含必要 `.bib`/`.cls`/图片），不是 PDF、截图或不可编译片段。
- [ ] 目标模板下 `-halt-on-error` 至少一次编译通过；未通过则记录首个未被修复根因（文件:行号）。
- [ ] 每条编译报错有 `文件:行号 + 分类 + 根因`；报错级联经 `latex_tools.py classify-log` 做根因判定。
- [ ] 公式符号/上下标/正斜体、变量与单位与原文一致（逐字复用，不重写单位）。
- [ ] 表格数值、列宽、对齐、三线表样式符合原文与模板，不靠缩放掩盖溢出。
- [ ] `\ref`/`\eqref`/`\cite` 全部解析（`refcheck` 的 undefined_refs/undefined_cites 为空或有明确处置）。
- [ ] 模板必填项（标题/作者/摘要/关键词/引用样式/文件结构）逐项覆盖。
- [ ] `build_manifest.json` 记录 TeX 引擎、宏包、字体、编译命令、轮次与每轮 log 指纹。
- [ ] result.json 每个 contract 字段有真实值或明确的 null/缺口说明。

## B. 使用增益（25 分：质量 / 效率 / 增益可验证）

- [ ] 修改集中在根因处，无无依据的全局替换；改动有 diff/说明，可回滚。
- [ ] 编译-修复循环有序：先分类、再定位、后修复、再编译验证。
- [ ] 每次编译都有 `compile_report.json` 可供复核。
- [ ] 相同输入重复运行得到一致产物与状态（确定性）。

## C. 创新增益（15 分：新能力 / 方法 / 证据）

- [ ] 事实库 factbase.json：数字/单位/引用/符号/论断带类型 + locator + 逐字 quote。
- [ ] 校验顺序反转：写 .tex 前先暴露事实与契约字段缺口（不是编译后才核对）。
- [ ] 报错分类（missing_package / undefined_control_sequence / math_error /
      undefined_reference / undefined_citation / font_error / capacity_exceeded /
      runaway_argument / unknown），并对级联做根因判定。
- [ ] 编译验证闭环：写稿 → 编译 → 解析 log → 修复 → 再编译，直至稳定或明确止损。

## D. 可靠体验（10 分：易用 / 稳定 / 异常处理）

- [ ] 空输入/坏 JSON/坏编码：退出码 0，写 `insufficient_evidence`/`input_error`，不崩溃。
- [ ] 缺引擎/宏包/字体/模板类缺失/权限不足：写入 `errors`/`degradations`，继续处理其余材料。
- [ ] 脚本路径用相对路径 `scripts/*.py`，`--help` 可执行。
- [ ] result.json 永远合法（可被 `json.load` 解析）。

## E. 生态贡献（10 分：文档 / 复用 / 推广）

- [ ] SKILL.md 写清用途、适用条件、调用方式、输入输出、异常处理。
- [ ] 脚本仅依赖标准库，可迁移；references/ 说明规则与查错方法。
- [ ] 示例输出明确标注「示例」，不冒充正式案例结果。

## 终止条件（P5 收尾前必查）

- [ ] result.json 含 case_id、status、sources、uncertainty、pending_human、change_log。
- [ ] 全部 output_contract 键原样回填（未完成=null/[]）。
- [ ] `status` 与实际情况一致：ok / partial / insufficient_evidence / input_error。
- [ ] 未编造数值、DOI、作者、实验结果、软件状态或评测分数。

## 常见查错方法

- 坏 JSON 输入：脚本按最长合法前缀解析并记录 `json_truncated_at_*`；不要手工重写。
- 坏编码：按 `utf-8-sig`→`utf-8`→`replace` 依次尝试；`decoded_with_replacement` 是降级信号。
- 缺引擎：`latex_tools.py compile` 返回 `engine_missing=True`，此时只交付可编辑 `.tex` 与诊断。
- 报错级联：先跑 `classify-log`，取 `root_causes` 第一条修复，再重编译，避免逐条改下游错误。

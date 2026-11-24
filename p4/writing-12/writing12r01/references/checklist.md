# 运行期自检清单（P3/P4 逐条勾选，映射评分维度）

本清单在 P3/P4 阶段逐条执行；未通过的项要么修复，要么写入 result.json 的
`uncertainty` / `pending_human` / `errors` / `degradations`，绝不静默跳过。
提交前运行 `python3 scripts/selftest.py`（无需真实 TeX 引擎）。

## A. 实际效果（40 分：完成度/正确性/完整性/可核验性）

- [ ] 主产物为可编辑 `.tex`（含必要 `.bib`/`.cls`/图片），不是 PDF、截图或不可编译片段。
- [ ] 在目标模板下用 `-halt-on-error` 至少一次编译通过；若未通过，记录首个未修复根因（文件:行号）。
- [ ] 每条编译报错都有 `文件:行号 + 分类 + 根因 + 修复策略`（见 `classify-log`），未停留在第一条。
- [ ] 公式符号/上下标/正斜体、变量与单位写法与原文一致（逐字复用，不重写单位）。
- [ ] 表格数值、列宽、对齐、跨页/三线表符合原文与模板要求，不靠缩放掩盖溢出。
- [ ] `\ref`/`\eqref`/`\cite` 全部解析（`refcheck` 的 undefined_refs/undefined_cites 为空或有明确处置）。
- [ ] 模板必填项（标题/作者/摘要/关键词/引用样式/文件结构）逐项覆盖。
- [ ] 记录 TeX 发行版、宏包、字体依赖、编译命令与轮次（写入 change_log/sources/compile_report.json）。
- [ ] result.json 每个 contract 字段有真实值或明确的 null/缺口说明。

## B. 使用增益（25 分：质量/效率/增益可验证）

- [ ] 修改集中在根因处，未做无依据全局替换；改动有 diff/说明，可回滚。
- [ ] 编译-修复循环有序：先分类、再定位、后修复、再编译验证。
- [ ] 每轮编译都有 compile_report.json 可供复核，减少盲改次数。
- [ ] 相同输入重复运行得到一致产物与状态（确定性；source_index.json 含 sha256）。

## C. 创新增益（15 分：新能力/方法/证据）

- [ ] 报错被分类（missing_package/missing_class/undefined_control_sequence/math_error/
      undefined_reference/undefined_citation/font_missing/table_error/option_clash/…）。
- [ ] 对报错级联做根因判定，并给出与分类对应的修复策略。
- [ ] 编译验证闭环：写稿 → 编译 → 解析 log → 修复 → 再编译，直至稳定或明确止损。

## D. 可靠体验（10 分：易用/稳定/异常处理）

- [ ] 空输入/坏 JSON/坏编码：退出码 0，写 `insufficient_evidence`/`input_error`，不崩溃。
- [ ] 缺宏包/缺字体/模板类缺失/权限不足：识别并写入 `errors`/`degradations`，继续处理其余材料。
- [ ] 脚本路径用相对路径 `scripts/*.py`，`--help` 可执行，仅标准库。
- [ ] result.json 永远是合法 JSON（可被 `json.load` 解析）。

## E. 生态贡献（10 分：文档/复用/推广）

- [ ] SKILL.md 写清用途、适用条件、调用方式、输入输出、异常处理、预算。
- [ ] 脚本仅依赖标准库，可迁移；references/ 说明规则与查错方法。
- [ ] 示例输出明确标注为「示例」，不冒充正式案例结果。

## F. 证据溯源（本版强化方向，硬性要求）

- [ ] evidence_map 每条 claim 有 id/type/statement/locator/quote/status/confidence。
- [ ] 每个关键数值/术语/引用/论断的 quote 都通过 `evidence.py verify` 逐字校验。
- [ ] `verify-map` 返回 ok=true（无 downgraded、无 missing_locator）。
- [ ] 冲突信息不隐藏：同一指标多来源不一致时全部保留并标 `source_conflict`。
- [ ] 缺口显式标注 `gap` 或 `pending_human`，绝不补造数值、DOI、作者或实验结果。

## 终止条件（P4 收尾前必查）

- [ ] result.json 含 case_id、status、artifacts、evidence_map、sources、uncertainty、pending_human、change_log。
- [ ] 全部 output_contract 键原样回填（未完成=null/[]）。
- [ ] `status` 与实际情况一致：ok / partial / insufficient_evidence / input_error。
- [ ] 未编造数值、DOI、作者、实验结果、软件状态或评测分数。

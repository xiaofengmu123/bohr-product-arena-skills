# 自检清单与查错速查（references，非正式案例结果）

本文件是运行期自检清单与 LaTeX 报错分类速查，供 Agent 在 P3/P4 阶段逐条勾选；未通过的项要么修复，要么写入 result.json 的 `uncertainty`/`pending_human`/`errors`/`degradations`，绝不静默跳过。

## A. 实际效果（40 分：完成度/正确性/完整性/可核验性）

- [ ] 主产物为可编辑 `.tex`（含必要 `.bib`/`.cls`/图片），不是 PDF、截图或不可编译片段。
- [ ] 在目标模板下用 `-halt-on-error` 至少一次编译通过；未通过时记录首个未被修复的根因（文件:行号）。
- [ ] 每条编译报错都有 `文件:行号 + 分类 + 根因` 说明，未停留在第一条错误信息。
- [ ] 公式符号/上下标/正斜体、变量与单位写法与原文一致（逐字复用，不重写单位）。
- [ ] 表格数值、列宽、对齐、跨页/三线表样式符合原文与模板要求，不靠缩放掩盖溢出。
- [ ] `\ref`/`\eqref`/`\cite` 全部解析（undefined_refs/undefined_cites 为空或有明确处置）。
- [ ] 模板必填项（标题/作者/摘要/关键词/引用样式/文件结构）逐项覆盖。
- [ ] 记录 TeX 发行版、宏包、字体依赖、编译命令与轮次（写入 change_log/sources）。

## B. 契约完整性（本版重点，对应"结果完整性/可核验性"）

- [ ] result.json 含 case_id、status、artifacts、sources、uncertainty、pending_human、change_log。
- [ ] `output_contract` 全部键原样回填（未完成=null/[]，不省略、不改名、不删未知键）。
- [ ] `contract_report` 与 result.json 逐字段一致；有值的字段 state=filled，null 字段 state=gap 并写入 uncertainty/pending_human。
- [ ] 每个契约字段的变更都有 change_log 记录（通过 `contract-set` 产生），可回滚、可复核。
- [ ] 每个关键数值/引用有 locator + 逐字 quote；无材料外新事实；冲突来源标 `source_conflict`。

## C. 使用/创新增益（25+15 分）

- [ ] 修改集中在根因处，未做无依据的全局替换；改动有 diff/说明，可回滚。
- [ ] 编译-修复循环有次序：先分类、再定位、后修复、再编译验证；每轮有 compile_report 可供复核。
- [ ] 报错被分类（见下表），而非只报一行。
- [ ] 对报错级联做根因判定，并给出与分类对应的修复策略。
- [ ] 相同输入重复运行得到一致的产物与状态（确定性）。

## D. 可靠体验/生态贡献（10+10 分）

- [ ] 空输入/坏 JSON/坏编码：退出码 0，写 `insufficient_evidence`/`input_error`，不崩溃。
- [ ] 缺宏包/缺字体/模板类缺失/权限不足：识别并写入 `errors`/`degradations`，继续处理其余材料。
- [ ] 脚本路径用相对路径 `scripts/*.py`，`--help` 可执行；`scripts/selftest.py` 三场景通过。
- [ ] result.json 永远是合法 JSON（可被 `json.load` 解析）。

## 报错分类速查（用于定位根因，不照抄）

| 分类 | 常见症状 | 修复策略 |
|---|---|---|
| missing_package | `! LaTeX Error: File 'xxx.sty' not found.` | 查发行版是否安装；缺失则写 degradations，改用等价宏包或注释并记录 |
| undefined_control_sequence | `! Undefined control sequence.` | 定位命令拼写、宏包未加载、自定义命令未定义 |
| math_error | `! Missing $ inserted.` / `! Double superscript.` | 检查数学环境配对、上下标分组、`\left\right` 配对 |
| undefined_reference | `! LaTeX Warning: Reference 'x' undefined` / `Citation 'y' undefined` | 补 `\label`/`\bibitem`，多遍编译直至收敛 |
| environment_mismatch | `! LaTeX Error: \begin{x} on input line … ended by \end{y}.` | 环境配对与嵌套顺序 |
| extra_alignment_tab | `! Extra alignment tab has been changed to \cr.` | 表格列数与 `&` 数不符、漏 `\hline` 或 `\\` |
| overflow | `Overfull \hbox (…pt too wide)` | 调整列宽/换行/`\resizebox` 仅作最后手段，不掩盖数值错误 |
| missing_class | `! LaTeX Error: File 'x.cls' not found.` | 记录 `engine_missing`/`missing_class`，交付可编辑源码并说明 |

## 终止条件（P4 收尾前必查）

- [ ] result.json 含 case_id、status、sources、uncertainty、pending_human、change_log。
- [ ] 全部 output_contract 键原样回填（未完成=null/[]）。
- [ ] `status` 与实际情况一致：ok / partial / insufficient_evidence / input_error。
- [ ] 未编造数值、DOI、作者、实验结果、软件状态或评测分数。

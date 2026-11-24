# 报错分类与根因判定规则（references，非正式案例）

本文件说明 `scripts/latex_tools.py classify-log` 的报错分类、根因判定与修复策略，
供 Agent 与使用者复核分类逻辑。所有类别在 `latex_tools.py` 的 `FIX_HINTS` 中一一对应。

## 错误分类表

| category | 触发特征 | 修复策略 |
|---|---|---|
| `missing_package` | 消息含 `.sty`/`.cls`/`.def` 且含 `not found`/`can't find`/`No file` | 安装对应宏包，或用可用的等价宏包替换 |
| `missing_file` | 消息含 `can't find file`/`not found`/`No file`（非宏包/文档类） | 恢复/引入被引用文件，或修正路径 |
| `undefined_control_sequence` | 消息含 `Undefined control sequence` | 定义该宏、修正拼写，或加载所需宏包 |
| `math_error` | `Missing $ inserted`、`Extra }`、`Double subscript/superscript`、`Missing \right` | 修正花括号分组、`$` 配对、上下标结构 |
| `undefined_reference` | `Reference ... undefined` | 补 `\label`、修正键名、重新编译收敛 |
| `undefined_citation` | `Citation ... undefined` | 补 `.bib` 条目、重跑 bibtex/biber、修正 cite key |
| `font_error` | `Font ... not found` / `fontspec error` | 安装字体，或替换为可用字体族 |
| `capacity_exceeded` | `capacity exceeded` / `Dimension too large` | 重构导致溢出/超界的构造，或调高相关限制 |
| `runaway_argument` | `Runaway argument` / `Paragraph ended before` | 修正未闭合的花括号 / 缺 `\end{...}` |
| `unknown` | 其余 | 查看出错行上下文定位根因 |

## 根因判定（级联错误）

编译报错常呈级联：一个未定义命令或缺失文件会派生大量下游报错。
判定规则：

1. 抽取每条 `! ...` 或 `file:line: message` 为独立错误条目，附 `文件:行号 + 分类 + 原文片段`。
2. 按 `(文件, 行号, 分类)` 去重并排序，**最先出现**的条目作为根因。
3. 同一 `文件:行号` 上由该根因派生的后续报错记为 `cascade_count`，不重复给出修复建议。
4. 修复顺序：先处理根因，再重新编译，而不是逐条改下游错误。

## 事实库（factbase）类型与溯源

`scripts/main.py extract` 产出 `/app/work/factbase.json`，每条 fact 含
`fact_id / type / value / locator / quote / span / confidence`：

- `numeric`：数字（含千分位、小数、科学计数法）及紧随单位；`unit`：单位 token。
- `citation`：`\cite{...}`、DOI、arXiv、PMID、`(Author, Year)`、`[1]` 引用。
- `ref`：`\ref`/`\eqref`/`\pageref` 等交叉引用键。
- `symbol`：含 LaTeX 命令的数学片段 `$...$`。
- `claim`：含数字的陈述句，供逐字回溯。
- `table_cell`：保留类型（表格单元归位时使用）。

`locator` 形如 `materials/paper.md#L12`；`quote` 为该行逐字片段。冲突检测采用保守规则：
**同一 locator、同一单位、不同数值量级**才标 `source_conflict`，避免把一行内多个不同数字误判为冲突。

## 校验顺序（与「编译优先」参考包不同）

本 Skill 的验证顺序是：**先抽取并校验事实与契约字段 → 再合成 .tex → 再进入编译循环**。
这样"编译得通但数字写错/引用写错"的情况在写稿前就被暴露，而不是编译通过后才发现。

# 编译错误分类与修复策略（taxonomy）

`latex_tools.py classify-log` 用本表对 `.log`/`.blg` 中的每条报错定位「文件:行号」并分类，
给出根因与修复策略。分类以「根因」而不是「第一条错误信息」为单位，用于识别报错级联。

## 错误分类

| category | 典型日志特征 | 根因 | 修复策略 |
|---|---|---|---|
| missing_documentclass | `File 'X.cls' not found` | 模板类文件缺失 | 提供/安装 .cls，或切换到可用 documentclass；写入 degradations |
| missing_package | `File 'X.sty' not found` | 所需宏包未安装 | 删除/替换对应 `\usepackage`，或安装宏包；记录 degradations |
| undefined_control_sequence | `Undefined control sequence` | 宏/命令未定义（拼写错或未加载宏包） | 检查拼写，补 `\newcommand` 或 `\usepackage` |
| math_error | `Missing $ inserted` / `Extra }, or forgotten $` / `Double superscript` / `Missing } inserted` | 数学定界符/花括号不匹配，上下标未加花括号 | 检查 `$...$`、`\(...\)`、align 环境、上下标花括号 |
| runaway_argument | `Runaway argument` | 未闭合的花括号/参数 | 定位并闭合失配的 `{ }` |
| emergency_stop | `Emergency stop` | 上游错误后的致命停止 | 修复第一条报错后重编译，忽略级联 |
| font_error | `Font ... not loadable` / `! Font ... not found` | 字体缺失 | 替换为系统可用字体或安装字体；记录 degradations |
| undefined_reference | `Reference 'X' on page N undefined` | `\ref` 目标缺少 `\label` 或名称错误 | 补 `\label` 或改正 `\ref` 名；重编译两遍 |
| undefined_citation | `Citation 'X' on page N undefined` | `\cite` 键不在 .bib | 补 bib 条目或改正 cite 键；跑 bibtex 后重编译 |
| package_error | `Package ... Error` | 宏包级错误 | 阅读宏包消息按其提示处理 |
| too_many_errors | `That makes 100 errors` | 报错级联（100 条） | 修复最前面的根因错误，忽略级联 |
| overfull / underfull | `Overfull \hbox` / `Underfull \hbox` | 盒子溢出/欠满（警告） | 调整列宽/换行；禁止靠缩放掩盖 |

## 级联判定原则

- 只把**第一条**（或最前面的独立）错误当作根因修复；其后由同一根因触发的错误标记为 `cascade`。
- 修复后再编译，观察错误数是否下降；若不降，说明根因判定有误，回读上下文重新分类。
- 警告（overfull/underfull/undefined 引用警告）单独记录，不阻塞编译成功判定。

## 降级决策矩阵（缺软件/缺数据时的处置）

| 情形 | 处置 | result.json 字段 | 是否可声称编译成功 |
|---|---|---|---|
| 无 TeX 引擎 | 交付可编辑 .tex + 诊断报告 | `degradations: engine_missing`、`limitations` | 否 |
| 缺宏包/字体 | 记录缺失清单与可替换方案，尝试去依赖修复 | `degradations`、`limitations` | 若修复后通过则如实记录，否则否 |
| 模板类缺失 | 交付 .tex 并标注模板缺失，说明迁移所需文件 | `degradations`、`pending_human` | 否 |
| 材料缺数值/引用/作者 | 正文留 `\GAP{...}`，不写推测值 | `facts: unverified`、`pending_human`、`limitations` | 与编译无关，不影响编译成功判定 |
| 材料冲突 | 两个来源都保留，标 source_conflict | `sources: source_conflict`、`uncertainty` | 与编译无关 |

原则：**编译状态与内容正确性分开报告**。编译通过不代表内容正确；内容缺口不靠编造填平。

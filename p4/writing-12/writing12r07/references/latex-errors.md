# LaTeX 报错分类与修复策略（鲁棒性重点）

本文件是 `latex_tools.py classify-log` 的分类依据与修复手册，Agent 在 P2 编译-修复
阶段据此行动。原则：**先定位（文件:行号）→ 再分类 → 后修根因 → 再编译验证**，
不盲改、不靠全局替换，不在第一条错误上止步。

## 分类表

| 类别 | 触发特征 | 根因 | 修复策略 |
|---|---|---|---|
| missing_document_class | `File '…' .cls not found` | 模板文档类缺失或未加入 TEXINPUTS | 确认 .cls 在 /input 或 TEXINPUTS；缺模板则交付可编辑 .tex 并记 `template_missing` |
| missing_package | `File '…' .sty not found` | 宏包未安装或加载顺序/版本不匹配 | 减非必要宏包、调整加载顺序；仍缺则记 `missing_package`，不谎报成功 |
| file_not_found | `File '…' not found` | `\input/\include/\usepackage` 目标缺失 | 补齐被引用文件或改正确路径 |
| undefined_control_sequence | `Undefined control sequence` | 命令未定义：宏包未加载/拼写错误/环境不匹配 | 补加载定义宏包或修拼写 |
| undefined_reference | `Reference/Citation '…' undefined` | 缺 `\label/\bibitem` 或多遍编译未完成 | 补标签并多遍编译至引用收敛 |
| math_error | `Missing $`、`Double superscript`、括号不配对 | 数学模式语法错误 | 成对检查 `$…$`/`\(…\)`/`\[…\]`、上下标与括号 |
| bad_environment | 环境名未定义或 `\begin/\end` 不配对 | 拼写或配对错误 | 核对环境名、保证 begin/end 成对 |
| runaway_argument | `Runaway argument?` | 参数未闭合（缺右花括号/结束标记） | 定位并闭合花括号/环境 |
| font_error | Font/T1/OT1/fontspec 报错 | 字体文件/字库缺失 | 安装字体或回退到可用字体声明 |
| capacity_exceeded | `TeX capacity exceeded`/`Dimension too large` | 容量超限或长度超界 | 检查超大长度/循环，简化表图 |
| emergency_stop | `Emergency stop` | 致命错误 | 以首条非 cascade 错误为根因修复 |

## 级联判定

- `classify-log` 把同一日志中的错误按出现顺序排列：**第一条为根因，其余标 `cascade: true`**。
- 修复时只先动根因，再重编译；根因消失后，多数级联错误会随之消失，避免盲改。
- 若第一条错误是 `undefined_reference`（可能只是警告性引用未定义），先判断是否为
  真正的编译阻断；非阻断警告不应阻止交付，但要写入 `uncertainty`/`limitations`。

## 鲁棒性约定（本版重点）

- 缺引擎：`compile` 记 `engine_missing` 并退出 0，**绝不谎报编译成功**；仍交付可编辑
  `.tex` + `compile_report.json` + 诊断说明。
- 缺宏包/字体/模板类：分类记入 `errors`/`degradations`/`limitations`，继续处理其余材料。
- 报错级联、日志缺失、引擎超时：分别记录，不崩溃；`result.json` 永远合法。
- 重复运行一致：固定引擎选择顺序（xelatex→pdflatex→lualatex）、固定编译轮次与命令、
  确定性文件排序与 JSON 键序（时间戳字段除外）。

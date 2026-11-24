# 报错分类与修复策略（查错方法）

本文给出 latex_tools.py 的报错分类规则、根因判定思路，以及与题目六个突破方向的对应修复策略。
本文件是查错方法与规则说明，不含正式案例答案。

## 1. 分类表

| 分类 | 典型 log 特征 | 根因判定 | 对应突破方向 |
|---|---|---|---|
| `missing_package` | `! Package ... not found`、`sty not found` | 宏包未安装/拼写错误 | 源码结构与宏包 |
| `undefined_control_sequence` | `! Undefined control sequence` | 命令拼写、宏包缺失导致命令未定义 | 源码结构与宏包 |
| `math_error` | `! Double superscript`、`Misplaced`、`$` 不配对 | 公式上下标、分隔符不配对 | 数学公式 |
| `undefined_reference` | `LaTeX Warning: Reference/Citation ... undefined` | label/cite 缺失或未多遍编译 | 引用与参考文献 |
| `missing_file` | `! LaTeX Error: File ... not found` | 子文件/图片/模板类缺失 | 模板适配 / 源码结构 |
| `environment_error` | `\begin{...} on input line` 未闭合 | 环境未配对、环境名错误 | 表格与浮动体 / 源码结构 |
| `template_conflict` | class 报错、必填项未填、选项冲突 | 模板必填项缺失或参数冲突 | 模板适配 |

## 2. 根因判定顺序

1. 先看 `compile_report.json` 的 `success` 与 `exit_codes`：失败才进入分类。
2. 对 `.log` 逐行分类，统计 `categories`；**报错级联时取最先出现的未修复类别**作为根因，不追尾随噪声。
3. 用 `refcheck` 单独确认 `undefined_refs`/`undefined_cites`，与 log 分类交叉验证。
4. 修复后重新编译，比较 `categories` 是否减少；未减少则说明修复未命中根因。

## 3. 修复策略（按分类）

- `missing_package`：列出缺失宏包，优先用模板自带的替代宏包或移除无关宏包；无法安装时记 `degradations`，不声称编译成功。
- `undefined_control_sequence`：对照模板文档确认命令名与参数；拼写错误直接修正；依赖宏包缺失先修宏包。
- `math_error`：逐字对照材料重写公式，保持上下标/正斜体/单位写法；多行公式用 `align`/`gather`，编号用 `\label`+`\eqref`。
- `undefined_reference`：补 `\label`/`.bib` 条目或修正引用名；多遍编译收敛后再复检。
- `missing_file`：按实际文件名扫描 `\input`/`\includegraphics` 路径；缺失模板类记录 `template_conflict`。
- `environment_error`：配对 `\begin`/`\end`，检查表格列数、`\multirow`/`\multicolumn` 参数。
- `template_conflict`：逐项覆盖模板必填项（标题/作者/摘要/关键词/引用样式），按模板要求组织文件结构。

## 4. 编译闭环（可复现）

每次编译都写入 `compile_report.json`，字段含：`engine`、`engine_path`、`requested_passes`、
`passes_run`、`exit_codes`、`success`、`classification`、`refcheck`、`generated_at`。
他人可按同一 `engine` + 编译命令 + 轮次复现；`success=false` 时绝不标注 `ok`。

## 5. 示例输出（示意，非正式案例）

```json
{
  "success": false,
  "engine": "pdflatex",
  "passes_run": 1,
  "exit_codes": [1],
  "classification": {
    "categories": {"undefined_control_sequence": 1},
    "errors": [{"line": 42, "raw": "! Undefined control sequence.", "category": "undefined_control_sequence"}]
  }
}
```

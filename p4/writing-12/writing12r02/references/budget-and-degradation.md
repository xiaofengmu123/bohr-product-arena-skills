# 时间预算、早落盘与降级阶梯（本变体的方法说明）

本变体与通用 LaTeX Skill 的关键差异：把「900 秒 / 64 请求」当成一等公民
来治理，而不是把它当作一条提示语。核心机制是**阶段记账 + 早落盘 + 硬止损**。

## 1. 六阶段硬预算

| 阶段 | 时间窗 | 秒 | 请求 | 落盘产物 |
|---|---|---|---|---|
| bootstrap | 0–60s | 60 | 3 | result.json 骨架、inventory.json、state.json |
| discover | 60–120s | 60 | 4 | source_index.json |
| draft | 120–240s | 120 | 16 | main.tex、.bib、contract 初稿值 |
| compile_fix | 240–660s | 420 | 30 | compile_report、修复后的 .tex |
| verify | 660–840s | 180 | 8 | sources/uncertainty/pending_human 回填 |
| finalize | 840–900s | 60 | 3 | 终版 result.json + PDF 预览 |

`scripts/main.py plan --work /app/work` 随时输出 `elapsed_s`、`remaining_time_s`、
`used_requests`、`remaining_requests`、`must_downgrade`。Agent 在每阶段开始前
先 `checkpoint begin --phase ...`，结束后 `checkpoint commit --phase ... --requests N`。

## 2. 早落盘：为什么把 partial 骨架写进前 3 分钟

- 评分要求「partial 结果必须标明边界」且不得留下空目录/半截 JSON。把骨架
  在 bootstrap（t≈0）原子写入，任何崩溃/超时都至少有一个可加载的诚实结果。
- 骨架即「诚实默认值」：contract 字段为 `null`，`status=partial`（无 contract
  时为 `input_error`），`errors` 记录缺 case.json、坏 JSON、缺 output_contract。
- 后续阶段只对同一 result.json 做**单调改进**：先填初稿值，再填编译状态，
  最后填证据映射。绝不推翻已写入的事实记录，只追加 `change_log`。

## 3. 硬止损（预算守护）

`BudgetGovernor.must_downgrade()` 在以下任一条件成立时返回真：

- 剩余时间 < 120 秒；
- 剩余请求 < 10 次。

此时立即执行 `finalize`，把 status 设为 `partial` 或 `insufficient_evidence`
（不得写 `ok`），把未完成项写进 `pending_human`，把超限原因写进
`degradations`。这样「没能做完」与「没做完却谎报 ok」被明确区分。

## 4. 降级阶梯（如实标注 limitations，不编造）

| 场景 | 记录位置 | status | 产物 |
|---|---|---|---|
| 无 LaTeX 引擎 | `degradations: engine_missing` | partial | 可编辑 .tex + 诊断，不声称编译成功 |
| 缺宏包/字体 | `degradations: missing_package/font_missing` | partial | .tex + 分类后的 log |
| 材料不足 | `uncertainty` + `pending_human` | insufficient_evidence | 缺口清单，绝不补造数值 |
| 来源冲突 | `sources[].conflict=true` | partial | 双方证据都保留 |
| case.json 坏/缺 contract | `errors` | input_error | 元数据骨架 + 缺失字段说明 |
| 写盘失败 | 最小合法 result.json | partial | 元数据 + 缺口，进程不崩溃 |

## 5. 编译-修复闭环与报错分类

`classify-log` 把 `.log` 解析为分类 findings（missing_package /
undefined_control_sequence / math_error / runaway_argument / font_missing /
capacity_exceeded / undefined_reference / undefined_citation /
multiply_defined_label / overfull / underfull），并给出 `root_cause`（首个
非警告错误，含文件:行号），用于区分「首因」与「级联错误」。修复策略：
先处理 `root_cause`，再编译验证 `rerun_needed`（交叉引用是否收敛），
不盲改、不靠缩放掩盖。

## 6. 确定性约定

- 固定编译命令与轮次（默认 3 遍，`-halt-on-error`），固定输入扫描顺序
  （目录名与文件名排序）。
- result.json 原子写盘（temp + rename），重复运行结构字段一致
  （`selftest.py` 的 `test_determinism` 校验；时间戳仅出现在 `change_log`
  与 `runtime`，不参与结构比较）。

## 7. 错误分类 → 修复策略速查表

`classify-log` 输出的每个 finding 都带 `category`；修复按「先根因、后级联」执行。

| 分类 | 典型 log 特征 | 修复策略 |
|---|---|---|
| missing_class | `File \`xxx.cls' not found` | 定位模板 `.cls` 是否随材料提供；缺失则标 `degradations`，不冒充编译成功 |
| missing_package | `File \`xxx.sty' not found` | 核对宏包是否在发行版；可用标准替代或写 `degradations`，不做全局猜测替换 |
| undefined_control_sequence | `Undefined control sequence` | 检查拼写、是否漏 `\usepackage`、是否用错环境名；修复该处而非加补丁命令 |
| math_error | `Double subscript`/`Missing $ inserted`/`Extra }` | 检查上下标层级与花括号配平，逐字符对照原意 |
| runaway_argument | `Runaway argument?` | 检查参数缺 `}`/`]`，回到报错行对齐花括号 |
| font_missing | `fontspec error`/`Font ... not found` | 查可用字体（font-discovery），缺字体写 `degradations` |
| capacity_exceeded | `TeX capacity exceeded` | 检查是否误入无限循环/过深嵌套，定位具体结构 |
| undefined_reference | `Reference \`x' undefined` | 核对 `\label` 是否与 `\ref` 同名，多遍编译看是否收敛 |
| undefined_citation | `Citation \`x' undefined` | 核对 `.bib` key 与 `\cite` 一致，跑 bibtex/biber 后再编译 |
| multiply_defined_label | `Label \`x' multiply defined` | 删除重复 `\label`，保留唯一键 |
| overfull/underfull | `Overfull \hbox`/`Underfull \hbox` | 调整断行、列宽或浮动体位置，不用 `\resizebox` 掩盖 |

每个修复后重跑编译并检查 `rerun_needed`（交叉引用是否收敛），直到 `refcheck`
的 `undefined_refs`/`undefined_cites` 为空或明确记录为 `pending_human`。

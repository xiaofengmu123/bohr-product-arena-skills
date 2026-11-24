# 确定性、证据溯源与降级规则

本文件说明 Skill 如何满足「同输入两次产物一致」以及「事实可逐条回溯」两条硬约束。
这些规则是评分维度「结果可核验性」「增益可验证性」「运行稳定性」的实现依据。

## 1. 确定性规则

**目标**：同一份 `/input`、同一运行环境，两次运行得到字节级一致的
`result.json` 与主产物（`.tex`/`.bib`/`.cls`）。

实现手段（全部落在 `scripts/core_impl.py`）：

1. **确定性 token 替代墙钟时间**：`run_id = sha256(排序后的输入文件 relpath + 内容哈希) 前 16 位`。
   `change_log[].at` 与 `runtime.run_id` 一律用 `run_id`。任何墙钟时间、随机数、
   进程号、对象 id 都不得写入产物文件。
2. **排序与规范化**：目录遍历按 relpath 排序；`discover_input`、`sources`、
   `uncertainty`、`pending_human`、`errors` 等列表在 `finalize` 时排序；
   JSON 写盘用 `sort_keys=True`、`ensure_ascii=False`、`newline="\n"`。
3. **固定编译命令与轮次**：`compile --passes N` 固定轮次与 `-halt-on-error
   -interaction=nonstopmode -file-line-error` 参数；`compile_report.json` 记录引擎、
   命令与每轮退出码，保证第三方可复现。
4. **自检闭环**：`scripts/selftest.py determinism` 在临时目录对同一合成输入跑两遍
   pipeline，比对 result.json 与 main.tex 的字节；不一致即失败（退出码 1）。

**边界**：确定性保证的是「同输入 + 同环境」；跨机器若 TeX 发行版/宏包版本不同，
PDF 字节可能不同，但可编辑 `.tex` 与 result.json 的确定性不依赖引擎版本，仍成立。

## 2. 证据溯源规则

- 每个关键数值、术语、引用、图元、论断，都要在 `sources` 中留一条
  `{"locator": "materials/xxx.md#L12", "quote": "逐字片段", "usage": "..."}`。
- `quote` 由 `scripts/main.py evidence quote --file PATH --start N --end M` 从实际文件
  逐字抽取，**不重写、不转述、不补造**。行号以 1 起，locator 相对 `/input`。
- 材料冲突不隐藏：同一指标两个来源不一致时，两条都保留并标 `"conflict": true`；
  result.json 的 `uncertainty` 记录冲突项。
- 材料没有的数值/作者/DOI/实验结果：写 `MATERIAL GAP` 或 `not_reported`，并列入
  `pending_human`；不得用文件名、领域标签或常识猜测补全。

## 3. 降级规则

| 场景 | 处置 | status / 字段 |
|---|---|---|
| 空输入目录 | 退出码 0，写合法 result.json | `insufficient_evidence` + `pending_human` |
| 坏 JSON / 坏编码 | 容错解析（截断/替换），记录错误继续 | `input_error` 或 `partial` + `errors` |
| 缺 TeX 引擎/宏包/字体 | `compile` 记录 `engine_missing`/分类错误，不声称编译成功 | `degradations` + `limitations`，仍交付可编辑 `.tex` |
| 材料不足/冲突 | 保留冲突、列缺口，不补造 | `insufficient_evidence` / `source_conflict` + `pending_human` |
| 写盘失败 | 降级到最小合法 result.json（元数据 + 缺口） | `partial` + `errors` |
| 剩余预算不足（<10 请求或 <120s） | 立即 finalize | `partial`/`insufficient_evidence` |

**诚实原则**：`status`、`limitations` 必须与软件实际状态、材料实际可得性一致；
评测分数、软件状态、科学数据一律不虚构。所有示例输出必须标注「示例」。

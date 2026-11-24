# 已知失败模式与处置

| 场景 | 表现 | 处置 | 结果状态 |
|---|---|---|---|
| 输入目录为空 | `scan_inputs` 无材料、无 case.json | 写合法 `result.json`，退出码 0 | `insufficient_evidence` |
| case.json 缺失 | `case_id` 回退文件名 | 记 `errors`，继续按兜底契约输出 | `input_error` |
| 坏 JSON / 坏编码 | `safe_json_load` 返回错误 | 捕获并记 `errors`，尝试去 BOM 再解析，继续处理其余材料 | `partial` |
| 单文件 >2MB | `read_text_robust` 返回 `size_exceeded` | 跳过该文件，记 `degradations` | `partial` |
| 二进制 PDF | `%PDF` 头检测 | 脚本不解析；用平台 pdf-text-tool，仍失败记 `limitations` | `partial` |
| 编号重复/缺口 | `fig_table_numbering` 告警 | 逐条核对，改一处重跑 `check` | `partial`→`ok` |
| 交叉引用悬空 | `crossref` 告警 | 补目标或改引用编号 | `partial`→`ok` |
| 同指标数值冲突 | `value_conflict` 告警 | 两个来源都记录并标 `source_conflict`，人工定夺 | `partial` |
| 引用缺文献条目 | `citations` 提示 | 补 `pending_human`，不补造 DOI/文献 | `partial` |
| 缺 Office/LaTeX/渲染器 | 无法出 docx/PDF 预览 | 改交 `.md`+`.json`，`degradations` 说明缺少的预览 | `partial` |
| 写盘失败 | 目录只读/磁盘满 | 降级写最小合法 `result.json`，进程不崩溃 | `partial` |
| 材料不足无法判断 | 无事实底稿 | 明确 `pending_human`，不编造示例填充 | `insufficient_evidence` |

## 通用原则

1. **先落盘骨架**：任何分析前先写 `status=partial` 的 `result.json`，中途异常也留下合法产物。
2. **状态诚实**：`ok` 仅在主产物与证据齐备时用；有缺口用 `partial`；无材料用 `insufficient_evidence`。
3. **不编造**：数据、引用、DOI、渲染状态、评测分数一律不伪造；缺失写 `null`/`not_reported`/`pending_human`。
4. **可复现**：脚本只用标准库、无随机项；同一输入重复运行结果一致。
5. **冲突不隐藏**：同指标两来源不一致时，两个都记录并标 `source_conflict`。

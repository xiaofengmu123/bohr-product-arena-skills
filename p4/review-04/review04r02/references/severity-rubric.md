# 严重度、类型与证据分级

## 严重度（severity）——回答三问后定级

每条 finding 的 `reasoning` 必须依次回答三个问题，再据此定级，禁止一律标"中"：

1. **是否影响主结论**：这条问题是否动摇论文的核心主张？
2. **是否可低成本修正**：补证据/改一句话能否修正，还是需要重做实验？
3. **是否影响可复现性**：他人能否据此复现或核查该结论？

| severity | 判定标准 | 处理优先级 |
|---|---|---|
| critical | 影响主结论且难以低成本修正，或使结论不可复现 | 必须先处理 |
| major | 影响主结论但可修正，或显著影响可复现性 | 应处理 |
| minor | 不影响主结论，局部证据不足/表述过度，可局部修正 | 可后续处理 |
| advisory | 非缺陷，仅为改进建议 | 可忽略 |

## 类型（type）——对应六类检查项

| type | 含义 | 覆盖维度 |
|---|---|---|
| overclaim | 结论强度超出数据能支撑的范围 | 论证链/事实 |
| unsupported_claim | 主张缺少支撑证据 | 论证链/引用 |
| citation_mismatch | 引用文献不支持所引那句话 | 引用支持 |
| method_gap | 方法回答不了研究问题 | 方法设计 |
| statistical_error | 统计口径与结论方向不一致 | 统计报告 |
| argument_gap | 推理跳跃、隐含假设未说明 | 论证链 |
| consistency_gap | 跨章节不一致、承诺未交付 | 一致性 |
| norm_issue | 学术规范声明缺失 | 学术规范 |
| advisory | 非缺陷的建议性意见 | 任意 |

## 状态（status）与置信度（confidence）

| status | 含义 |
|---|---|
| confirmed | 依据原文即可确认成立 |
| probable | 高度疑似，但需外部/补充信息确认 |
| pending_review | 无法确认，需外部核对（引用全文、原始数据等） |
| ruled_out | 核查后排除（可保留作复核记录） |

- `confidence` 表示判断本身的确信度：`high | medium | low`。
- 引用全文拿不到、数值无法外部核验时，一律 `pending_review`，并写入 `pending_review[]`，不降级成"确认"。

## 证据映射与逐字定位（evidence-lock）

- 每条 finding 必须含：`locator`（如 `p3¶7` 或 `L10`）、逐字 `quote`、`claim`、`evidence`、`reasoning`、`suggestion`。
- `quote` 必须逐字复制语料原文；改写后的摘录视为不可复核，`selfcheck` 不通过。
- 冲突信息不隐藏：同一指标两个来源不一致时两处都记录，并在 `evidence` 标 `source_conflict`。

## 修改建议（suggestion）——落到具体句子

建议必须回答至少一项：补什么证据、改哪句话、把哪个结论的强度降下来、换成什么表述。
禁止"建议补充论证""建议加强逻辑"这类仍需人工重写的空话。

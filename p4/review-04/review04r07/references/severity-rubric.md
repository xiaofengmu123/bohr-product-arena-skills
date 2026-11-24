# 严重度、类型与状态分级标准

`scripts/main.py update` 只接受下列枚举，非枚举值会回落为 `minor` / `suggestion` /
`pending_review` 并在 `pending_review` 里记录。本文件给出**可解释的分级依据**。

## 1. 严重度（severity）——三问判据

每条 `reasoning` 必须显式回答三问，严重度由三问结果推出：

1. **是否影响主结论**（会否推翻/削弱一个核心结论，还是仅局部表述）；
2. **是否可修正**（能否通过补数据/改一句/降级措辞修复，还是要推翻重做）；
3. **是否影响可复现性**（读者能否据此复现实验/核对数字）。

| severity | 判据 | 示例 |
|---|---|---|
| `critical` | 影响主结论 且 几乎不可修正，或阻断可复现 | 方法根本回答不了研究问题；主结论方向与统计结果相反 |
| `major` | 影响主/支撑结论 或 影响可复现，但可修正 | 结论缺效应量或 CI；引言承诺未交付 |
| `minor` | 局部问题，不影响主结论 | 单句措辞过强但已有限定；图表编号/单位笔误 |
| `suggestion` | 非缺陷，可选改进 | 建议补充敏感性分析、补充替代解释讨论 |

**禁止一律标 `minor`/`medium`**。严重度必须可解释；无法确认影响范围的，标 `pending_review` 而不是随便给级。

## 2. 类型（type）

| type | 含义 |
|---|---|
| `overclaim` | 结论强度超出数据/证据能支撑的范围 |
| `citation_unsupported` | 所引文献（很可能）不支持所引那句话 |
| `method_mismatch` | 研究方法回答不了研究问题 |
| `stat_misdirection` | 统计口径/方向与结论表述相反或不一致 |
| `missing_delivery` | 引言承诺的问题在结果/讨论未交付 |
| `causal_from_correlational` | 相关性设计用了因果表述 |
| `inconsistency` | 摘要/正文/图表/跨章节前后不一致 |
| `evidence_gap` | 主张缺少必要证据（但结论未必然超限） |
| `norm_issue` | 可核验的学术规范问题 |
| `suggestion` | 建议性意见，非缺陷 |

## 3. 状态（status）与置信度（confidence）

- `status`：
  - `confirmed`：材料内部可直接证明；
  - `probable`：证据较强但需一点外部信息才能坐实（如引用支持性）；
  - `pending_review`：无法在现有材料/工具内确认，需人工或外部核对；
  - `ruled_out`：经查证不成立（用于记录"排查后排除"的条目）。
- `confidence`：`high` / `medium` / `low`，与 status 匹配；`pending_review` 通常配 `low`。

## 4. 可执行修改建议（suggestion）的写法

每条建议必须落到具体句子，模板三选一：

1. **补证据**："在『[quote 所在句]』后补报告效应量 d 与 95% CI，或注明样本量。"
2. **改措辞**："把『proves』改为『is consistent with』，并把『causal』改为『associated with』。"
3. **降强度**："将结论从『X 显著改善 Y』降级为『在本样本中 X 与 Y 改善相关，因果待进一步验证』。"

禁止出现"建议加强论证""建议补充讨论""建议更严谨"这类需要人工重写的空建议。

# 降级与不确定性规范（本 Skill 的侧重点）

本 Skill 与常见深度研究流程的核心差异：**不确定性不是结论的补充说明，而是与结论同级的、必须逐条落盘的证据结构**。评分维度中「冲突证据」与「研究空白」能否得分，取决于评审能否看到：①冲突双方各自的来源与定位；②每条空白到底是「检索过没查到」还是「根本没查」还是「材料里没有」。

## 1. 不确定性的四级结构

`result.json` 的 `uncertainty` 固定包含四个子表，全部可为空数组但字段必须存在：

| 字段 | 记录内容 | 何时非空 |
|---|---|---|
| `conflicts` | 两个及以上来源对同一指标/结论给出不一致结果 | 材料中出现相互冲突的数值、方向、结论 |
| `biases` | 材料与检索过程可能引入的系统偏倚 | 来源单一、语言偏倚、时间窗偏倚、发表偏倚、样本代表性 |
| `gaps` | 检索后发现证据缺失的点 | 检索过但无命中、或明确未检索 |
| `undetermined` | 材料之间能读但无法裁定的点 | 冲突无公认裁定、或材料信息不足 |

## 2. 关键规则：gap 必须带 `kind` 与 `search_evidence`

「研究空白」写成套话是本题明确的扣分点。因此每条 gap 必须声明它属于哪一类，并给出检索证据：

```json
{
  "id": "G1",
  "question": "该干预的长期安全性",
  "kind": "retrieval_empty | not_searched | material_missing",
  "search_evidence": {
    "searched": true,
    "search_log_ids": ["SL3"],
    "queries": ["intervention long-term safety randomized"],
    "result_count": 0,
    "conclusion": "在限定的来源范围内无命中"
  }
}
```

- `retrieval_empty`：执行过检索（有 `search_log_ids` 指向检索记录）但返回空，表示「已查、未找到」。
- `not_searched`：因预算、依赖未满足或范围明确排除而没有查，须说明原因。
- `material_missing`：题目材料里应出现但没有出现的信息，属输入缺口。

只有前两类 gap 能支撑「研究空白」的结论；`material_missing` 只能进入 `pending_human` 或 `limitations`，不能当作已论证的空白。

## 3. 冲突证据的并列规则

- 冲突条目必须给出 `sources` 数组，且**至少两个来源**，每项带 `source`、`locator`、`quote`。
- 允许三种处置，必须在 `resolution` 字段声明：`resolved`（有明确裁定依据）、`unresolved`（材料内无裁定）、`undetermined`（无法裁定）。
- **禁止静默择一**：只要判定某条结论最终采纳了冲突中的一方，`resolution` 必须写明「采纳 X，因为…；未采纳 Y，因为…」。未写处置的冲突属于 `undetermined`。
- 如果一篇综述本身就把矛盾写成「尚存争议」而没有任何来源定位，这不算已处理的冲突；应记为 `undetermined` 并说明「原始文献未给出双方定位」。

## 4. 证据强度与断言类型的对应

每条结论用 `strength` 标注，每条证据用 `claim_type` 标注，两者必须自洽：

| 结论 `strength` | 支撑证据的 `claim_type` | 允许条件 |
|---|---|---|
| `established` | `fact` | 至少一条 `judgment=supports` 且 `verification=verbatim` 的证据 |
| `inferred` | `inference` | 证据为合理推断，或仅有 `partial` 支持 |
| `conjectural` | `gap` 或单源弱证据 | 明确为推测，不得使用「表明/证实」类动词 |
| `undetermined` | 冲突且未裁定 | 进入 `uncertainty.undetermined` |

**动词与证据强度挂钩**：`established` 可用「表明/证实」；`inferred` 用「提示/初步表明」；`conjectural` 用「可能/尚待验证」。一条结论若没有 `verbatim` 证据支撑，不得写成 `established`。

## 5. 降级（degradation）如实化

任何读取、解析、检索失败都必须同时出现在两处：

1. `degradations` / `errors`：机器可读的失败明细（哪一步、哪个文件、什么错误）。
2. `limitations`：人类可读的影响说明（该失败如何影响结论范围）。

规则：

- 材料缺失 → `status=insufficient_evidence`，`pending_human` 列出缺什么，`limitations` 说明已用哪些来源的哪些部分。
- 软件缺失（如无 OCR、无浏览器）→ 记入 `limitations`/`errors`，**不得**声称「已读取像素值」或「已抓取网页」。
- PDF 无文本层 → 该证据 `judgment=extraction_failed`，`pending_human` 建议 OCR 或人工补录，不硬读像素。
- 检索限流/失败 → `search_log` 记录失败与重试，`limitations` 说明覆盖范围受何限制。
- 缺数据、缺软件、缺权限时**不编造**：任何数字、DOI、文献、运行结果若材料中没有，一律写 `not_reported` / 缺口，而不是造一个近似值。

## 6. 自洽性检查（finalize 前必做）

1. 每条结论的 `evidence_ids` 都能在 `evidence_matrix` 中命中。
2. 每条证据的 `quote` 若是 `verification=verbatim`，必须逐字来自材料；`scripts/main.py selfcheck` 会对纯文本源做子串校验。
3. `uncertainty.gaps` 中 `kind=retrieval_empty` 的条目必须能指到 `search_log.jsonl` 的真实检索记录。
4. `status=insufficient_evidence` 时，`gaps`/`pending_human`/`limitations` 至少一项非空。
5. `errors`/`degradations` 非空时，`limitations` 必须非空。

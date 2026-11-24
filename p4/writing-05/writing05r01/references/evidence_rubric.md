# 证据溯源与强度分级口径（evidence_rubric）

本文件是 `evidence.py` 与 Agent 判定共用的事实口径。所有取值必须取自以下枚举，禁止自由造词。

## 1. locator 格式

- 规范格式：`S{section}.P{para}.s{sentence}`，如 `S2.P3.s1`。
- 无法分节：`P{para}.s{sentence}`；连句子都无法切：`P{para}` 或字符区间 `P2[120:180]`。
- `section` 由标题启发式切分（markdown `#`、`第X章/节/部分`、`X、`、全大写短行）；切分不确定时在 `limitations` 注明"section 边界为启发式切分"。
- `sentence` 按 `。！？!?；;` 切分；**英文句点 `.` 不切分**（避免小数点、缩写如 `e.g.`/`et al.` 误切），故英文段落内多个句号句会合并为一个 `s1`。需要句子级定位时，可人工缩小 quote 并手写句子序号；`validate` 以段落级对齐为容差（校验 quote 是否位于 locator 指向的段落内）。
- locator 必须能从 `/app/work/locator_index.json` 查到，否则 `validate` 会报"未登记/格式非法"。

## 2. 逐字 quote 硬要求

- 每条 support 的 `quote` 必须是源文本的**逐字片段**，不得改写、不得翻译、不得拼接。
- 允许的容差仅限空白折叠与全/半角归一化（`evidence.normalize_ws`），其余差异一律视为不一致。
- `validate` 会对每条 quote 做逐字回查：找不到即报 `quote 与源文本逐字不一致`。
- 图表、公式若无法逐字摘录：`quote` 写 `"<figure/table not extractable>"`，`verdict=pending_human`，并在 `note` 说明该处确有图/表但无法读取，不得猜测内容。

## 3. 论断类型 claim_kind

| 值 | 含义 | 表述禁忌 |
|---|---|---|
| `fact` | 可复核的事实/数值 | 不得加因果 |
| `inference` | 由证据推理得到的结论 | 标注推理依据 |
| `method` | 方法/流程/设计 | — |
| `comparison` | 组间/文献间比较 | 标注可比性与混杂 |
| `consensus` | 领域共识 | 需多源/综述/指南支持 |
| `causal` | 因果结论 | 仅 RCT/机制+剂量反应可支持，且注明 |
| `recommendation` | 建议/推荐 | 标注证据等级与获益-风险 |

## 4. 支撑关系 relation

| 值 | 含义 |
|---|---|
| `direct` | 原文明确表述相同结论 |
| `indirect` | 需一步合理推断才支持 |
| `insufficient` | 原文相近但更弱/更窄，不足以支撑当前表述 |
| `contradicts` | 原文支持相反或明显不同的结论 |
| `no_support` | 原文未涉及该论断 |

## 5. 证据强度 strength（结合研究设计与验证程度，不只看单篇）

| 值 | 典型依据 |
|---|---|
| `high` | 大规模 RCT / 系统综述 / 荟萃分析 / 多中心一致结果 |
| `moderate` | 队列、病例对照、单中心 RCT、指南推荐 |
| `low` | 病例报告、小样本、专家意见、单篇观察性研究 |
| `insufficient` | 仅有摘要、二手转引、预印本未审、无法核实全文 |

`strength_rationale` 必须写明一句理由（如"单篇随机试验 n=1204，未独立复现"）。

## 6. verdict 取舍

| 值 | 含义 |
|---|---|
| `accept` | 证据直接支持，可照写 |
| `hedge` | 支持较弱，正文需加限定语（如"初步表明""与…相关""提示"） |
| `reject` | 证据矛盾或不足以支撑，应删除或改写 |
| `pending_human` | 无法判定/材料不足/需人工确认 |

## 7. 不确定性表达（说得准）

- 相关性不写成因果：观察性研究用"相关/关联/提示"，不用"导致/证明"。
- 单篇小样本不写成共识：用"某研究显示""初步表明"，不用"公认""已被证实"。
- 冲突不隐藏：同一指标来源不一致时，两条都记录，relation 标 `contradicts`，写入 `uncertainty`。
- 结论边界：`claim_scope` 写明人群/条件/时间；正文表述与其相称，不做超出材料的泛化。

## 8. 失败类型速查（写入 note / limitations）

| 失败类型 | 判定信号 | 处置 |
|---|---|---|
| `quote_mismatch` | quote 逐字回查不命中 | 改写 quote 为原文逐字片段，或改 verdict=reject |
| `source_unfindable` | source_doc 不在 source_map | 补全来源路径，或 verdict=pending_human |
| `strength_mismatch` | 弱证据写成强结论 | 降 strength 并在正文加限定语（hedge） |
| `causal_overclaim` | 观察性研究写"导致/证明" | 改"相关/提示"，claim_kind 改 inference |
| `hidden_conflict` | 同指标多源不一致只留一条 | 两条都记录，标 contradicts |
| `metadata_mismatch` | 著录/编号与参考文献表不符 | 逐条核对，标注差异 |
| `secondary_citation` | 二手转引冒充一手 | 标 secondary，evidence_type=other，pending_human |
| `retracted` | 来源为撤稿或版本过旧 | 标注撤稿/版本差异，换来源或 reject |
| `pdf_no_text_layer` | pdftotext 空/失败 | 记 limitation，OCR 或 pending_human，不猜内容 |
| `abstract_only` | 仅摘要可读，全文不可得 | strength=insufficient，verdict=pending_human |

## 9. 最小示例（示例，非正式案例）

一条论断对应一条来源的 `evidence_map` 条目：

```json
{
  "claim_id": "C1",
  "claim": "纳入成年患者的总有效率为 61.4%",
  "claim_kind": "fact",
  "claim_scope": "enrolled adults",
  "final_wording": "一项随机试验显示，纳入成年患者的总有效率为 61.4%（95% CI 57.8–65.0）。",
  "support": [{
    "source_doc": "materials/paper.pdf",
    "locator": "S2.P1.s1",
    "quote": "The overall response rate was 61.4% (95% CI 57.8-65.0).",
    "relation": "direct",
    "evidence_type": "primary",
    "strength": "moderate",
    "strength_rationale": "单篇随机试验，未独立复现",
    "verdict": "accept"
  }],
  "uncertainty": "单篇研究，不作共识表述",
  "citation_refs": ["[1]"],
  "status": "verified"
}
```

> 上例数值仅用于演示接口，不是任何真实案例的结果。


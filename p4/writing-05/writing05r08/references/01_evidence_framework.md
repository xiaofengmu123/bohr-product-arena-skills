# 证据强度分级与不确定性口径（01_evidence_framework）

本文件是 `evidence-driven-writing` 的判断口径。Agent 在 P2 阶段按此定级、写 `uncertainty`，并在 P3 成稿时把措辞与该级相称。口径只约束判断，不替代材料事实；材料里没有的信息不得补造。

## 1. 四个可复核问题（贯穿全程）
- 找得到：每条实质论断被提取出来，关联到具体文献的具体段落（locator + 逐字 quote），不停留在"有引用"。
- 对得上：文献真实存在、元数据准确、原文真的支持这句论断，而不是支撑一句相近但更弱/更强的话。
- 说得准：证据强度与不确定性如实表达；相关性不写因果、单篇小样本不写共识、冲突证据不隐藏。
- 复得现：论断编号、证据表、原文摘录、强度理由可独立回查与重跑。

## 2. 证据强度分级
结合研究设计、样本量、重复验证与共识程度，每级对应成稿措辞约束。

| 级别 | 判据（任一即入该级，取满足的最高级） | 成稿措辞要求 |
|---|---|---|
| `strong` | 大规模/多中心 RCT 或荟萃分析；或多项独立研究一致、结论被指南采纳 | 可用肯定式："证实""已证实""meta 分析表明" |
| `moderate` | 单中心 RCT、队列/病例对照设计、样本量一般；或少数研究一致 | 用"表明/提示"，注明人群与条件："在……人群中表明" |
| `weak` | 小样本、单臂、横断面、病例报告、动物/体外研究、预印本未复核 | 用"初步提示/尚需验证"，显式保留不确定性 |
| `insufficient` | 仅摘要、二手转引、来源不可得、原文不支撑该强度 | 不进入正文结论；如确需提及，标 `pending_human`/`uncertainty` |

定级理由必须写进 `strength_reason`（如"RCT，n=84，单中心"），不能只给级别不给理由。

## 3. 硬性表述规则（说得准）
- 相关性 ≠ 因果：claim_type 标 `causal` 仅当存在随机化/因果推断/剂量反应等因果证据；否则用关联表述并把 claim_type 标 `interpretation` 或 `fact`。
- 单篇小样本 ≠ 领域共识：`consensus` 类型需多源且方向一致；单源一律不标 consensus。
- 冲突证据不隐藏：同一论断存在方向相反来源时，全部并列写入 `sources`，`support` 标 `conflicts`，并在 `uncertainty` 说明分歧与可能原因。
- 限定语要"既不过头也不含糊"：允许"在……条件下/本样本中/需更大样本验证"，禁止"可能/也许"叠加到无法判断，也禁止无证据地升格为"证实"。

## 4. claims（论断清单）schema
```json
{
  "claim_id": "C1",
  "text": "可判定真假的一句话论断（原文意义，非缩写）",
  "claim_type": "fact | interpretation | causal | consensus | recommendation",
  "evidence_strength": "strong | moderate | weak | insufficient",
  "strength_reason": "定级依据：设计/样本量/一致性/共识",
  "uncertainty": "应落地为成稿限定语的表述",
  "sources": ["S1", "S3"],
  "needs_human": false
}
```

## 5. sources（证据表）schema
```json
{
  "source_id": "S1",
  "source_doc": "材料文件名",
  "source_type": "original_study | review | guideline | preprint | other",
  "metadata": {"authors": "", "year": "", "title": "", "journal": "", "doi": ""},
  "locator": "S3.P2.s1（统一格式；无法分节用 P{para} 或页码/字符区间）",
  "quote": "逐字原文摘录（不改写、不省略关键限定语）",
  "claim_ids": ["C1", "C2"],
  "support": "supports | partially_supports | weakly_supports | conflicts",
  "verification": "verified | unverified | conflict"
}
```

- `quote` 必须是逐字摘录；改写内容不能放进 quote，可放 `metadata.note` 或 claim 的 `strength_reason`。
- `locator` 统一 `S{section}.P{para}.s{sentence}`；材料无分节时用 `P{para}` 或 `page N`、字符区间 `[start:end]`。每条判断应能指回原文复核。
- 数字/术语/引用/图表/关键论断都要能回到某条 source；无法回溯的不得写入 deliverable。

## 6. 多对多匹配
一条 claim 可多源（共同支撑或相互冲突），一篇文献可支撑多条 claim。成稿内联标号用 `[C1]` 指认论断，参考书目按 source_id 列出；evidence 表用 `claim_ids`/`sources` 双向索引，保证可回查。

## 7. 不新增材料外事实
只写材料中实际出现的事实/数字/结论；文献元数据（作者/年份/DOI/卷期页码）仅在材料或可核验检索结果中出现时才写入，缺失写 `not_reported` 或留空，不凭记忆补 DOI 或卷期。

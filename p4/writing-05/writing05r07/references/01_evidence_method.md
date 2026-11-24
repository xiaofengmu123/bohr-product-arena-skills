# 证据驱动写作方法：论断清单 → 证据表 → 成稿

本文是 SKILL.md 的方法细则，Agent 执行 P2 分析时按此口径逐条落地。核心原则：**每句有依据的话都能被指认依据在哪、依据有多强**，而不是只挂一个引用。

## 1. 三段交付（缺一不可）

| 段 | 产物 | 关键字段 | 可复核标准 |
|---|---|---|---|
| 论断清单 | 把材料与拟写结论拆成可判定论断 | `claim_id`、`claim`（一句一断言）、`claim_type` | 每条断言独立为真/假，不含"以及"折叠多断言 |
| 证据表 | 论断 ↔ 来源映射 + 强度 | `claim_id`、`source_doc`、`locator`、`quote`、`strength`、`supports` | 用 quote 逐字复核，不新增材料外事实 |
| 成稿 | 依据强度措辞的稿件/改稿/结构化结果 | 体裁、受众、格式，正文与证据表一致 | 强度等级与措辞相称，越界处标注 |

三者必须同源：成稿中的每一处实质性论断都能在论断清单找到对应 `claim_id`，每一行证据表都能通过 `locator + quote` 回到原文档。

## 2. 找得到：论断切分

1. 通读全文，不拿摘要替代正文；PDF 用 `pdftotext -layout` 取文本。
2. 一句一断言；复合句拆到不能再拆为止。例："A 有效且安全"拆成两条：`A 有效`、`A 安全`。
3. 区分四类论断类型：
   - `fact`：可被单个数据点判定的描述（数字、方法、样本量、结果）
   - `inference`：对机制、因果、推广的解释
   - `comparison`：组间/文献间比较
   - `recommendation`：建议、结论、临床/政策含义
4. 每条给 `locator`，统一格式 `S{节}.P{段}.s{句}`；无法分节用 `P{段}.s{句}` 或字符区间 `@起-止`。凡引原文必带逐字 `quote`。

## 3. 对得上：来源匹配与引用校验

1. 每条论断至少一条来源；一条论断可有多来源，一个来源可支撑多条论断（many-to-many 都要能查到）。
2. 区分来源类型：`original_research` / `review` / `guideline` / `preprint` / `secondary_citation`（二手转引）。二手转引必须标 `secondary_citation` 并提示追原文献。
3. 引用校验四问：
   - 文献是否真实存在（标题/作者/期刊/年份/DOI 是否一致）？
   - 原文是否真的支持**这句**论断，还是只支持一句更弱或更强的话（过度/不足支撑要标 `supports: no|partial|yes`）？
   - 编号与参考文献表是否一一对应？
   - 是否有撤稿/版本差异/二手转引？
4. 元数据或原文无法核验时，写 `pending_human` 与 `uncertainty`，**不得**用记忆补 DOI、卷期页码。

## 4. 说得准：强度与措辞相称

强度分级口径见 `references/02_strength_grading.md`。硬规则：

- 相关性不得写成因果；观察性研究用"相关/关联/提示"，随机对照试验才可用"表明/证实"级别的表述，且仅在样本与终点支持时。
- 单篇小样本不得写成领域共识；共识表述只留给系统综述/Meta/指南层级，并写清是否"多项研究一致"。
- 冲突证据必须同时呈现，不隐藏；标 `source_conflict` 并写两条来源。
- 机制未明、适用人群未验证、样本受限处加限定语，不夸大也不含糊到无法判断。

## 5. 复得现：证据表结构与可重跑

每条证据表行至少含：

```json
{
  "claim_id": "C01",
  "claim": "treatment reduced mortality",
  "source_doc": "materials/material.md",
  "locator": "S1.P1.s2",
  "quote": "mortality was 18% vs 32% (p=0.01)",
  "source_type": "original_research",
  "strength": "rct",
  "supports": "yes",
  "rationale": "RCT, n=200, primary endpoint mortality",
  "needs_human": false
}
```

`supports` 取 `yes|partial|no|unverifiable`；`partial` 必须写明缺哪一半（例如只支撑了方向未支撑幅度）。判定理由 `rationale` 让第三方不看原文也能理解为何这样分级。

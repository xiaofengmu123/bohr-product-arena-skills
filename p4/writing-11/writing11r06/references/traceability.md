# 证据溯源与中间表示规范（traceability）

本 Skill 的核心设计取舍是**结构化中间表示**：在"读原文"与"写译文"之间，
强制落盘四类可复核的中间产物，让每条译文、每个数字、每处术语都能指回原文的
逐字片段（locator + quote），而不是散落在对话流里无法复现。

## 一、为什么需要中间表示

基础 Agent 直接输出译文时，常见三类不可复核问题：

1. 术语在摘要/正文/图题间反复重译，无统一依据；
2. 数字、单位、引用在翻译中被"顺手改写"，事后无法定位原值；
3. 增译/省译/否定范围/因果强度是否保持原意，无人可核。

结构化中间产物把"翻译决策"显式化：先记决策（方案/术语/对齐/核对），再产出译文。
复核者不重读全文即可定位每一处受影响段落。

## 二、四类中间产物（写入 /app/output/ 或 /app/work/）

### 1. plan.json —— 翻译方案（先计划，再动手）

```json
{
  "source_lang": "en", "target_lang": "zh",
  "direction": "en->zh",
  "output_form": "bilingual",          // mono | bilingual | revision
  "segmentation": "paragraph",
  "sections": [{"id":"abs","label":"摘要","src_chars":1200,"tgt_est_chars":1500,"style":"学术"}],
  "style_register": "学术论文，非宣传口吻，保留hedge",
  "budget": {"seconds":900,"requests":64},
  "glossary_scope": ["术语","缩写","上下位词"],
  "risks": ["超长截断","公式对齐","et al.格式差异"]
}
```

作用：把"体裁、受众、格式、语向、分段策略"在动手前固定，避免中途改向。

### 2. glossary.json —— 术语表（术语稳）

见 `checklist.md` 第六节模板。`locked=true` 的译法全篇锁定；`conflicts` 记录同一
英文词跨学科译法不同，按 `domain` 分列，不强行统一。

### 3. alignment.json —— 句段对齐（对得齐）

```json
{
  "units": [
    {"id":"seg-001","src":"...原文句...","tgt":"...译文句...",
     "position":"sec2:para1","status":"aligned"}
  ],
  "orphan_src": ["seg-018"],
  "orphan_tgt": []
}
```

`position` 用段落/图题/脚注/参考文献的位置标签，保证结构顺序不变；`orphan_*`
列出漏译/多译，必须清零或登记。

### 4. fact_check.json —— 数字/引用核对（数字实、边界清）

```json
{
  "numbers": [
    {"locator":"materials/paper.md#p3","quote":"42.7%","tgt_value":"42.7%",
     "status":"ok"}
  ],
  "citations": [
    {"locator":"materials/paper.md#p9","quote":"(Smith et al., 2021)",
     "kept":true,"status":"ok"}
  ],
  "flags": [
    {"kind":"negation","locator":"materials/paper.md#p7",
     "quote":"did not reach significance","reason":"否定范围需保留"}
  ]
}
```

`quote` 必须是原文逐字片段；`tgt_value` 是译文落地值；`status` 取
`ok | mismatch | needs_human`。

## 三、证据映射写入 result.json

`fact_source_map` 每项至少含 `item`、`kind`、`value`、`source`、`note`，
`source` 用 `materials/paper.md#p3` 或 `input.pdf:12` 形式给出 locator，
`note` 补逐字 `quote` 或"推断/待核"。`kind` 取
`number | term | symbol | citation | figure | claim`。

## 四、可复核性红线

- 不编造 locator：`quote` 必须真实出现在所引文件中，找不到就写 `not_found`。
- 冲突不隐藏：同一指标两处不一致时两条都记，标 `source_conflict`。
- 不新增材料外事实：译文的每个新增数字/结论都必须有 `source`，否则删去或
  标 `pending_human`。
- 局部修改只动受影响段落：用 `occurrences`/`position` 定位文件:行号，
  改动差异记入 `version_boundary.modifications` 的 `before_after`。

## 五、重复运行确定性

固定输入下两次运行应产出相同的四类中间表示与 `result.json` 骨架（JSON 字段、
顺序、时间戳除外）；无随机性。`scripts/selftest.py` 只做确定性校验，不生成内容。

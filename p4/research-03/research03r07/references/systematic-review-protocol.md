# 系统性文献综述协议（可复用流程规范）

本文档是 SKILL.md 的领域协议补充，定义五个环节的输入、输出、判定规则与可复用模板。脚本只负责确定性组装与校验，实质判断由 Agent 依据本协议写入 `/app/work/`，再由 `scripts/main.py finalize` 组装。

## 0. 总体原则

- 五环节缺一不可：**检索式 → 纳排标准 → 两轮筛选记录 → 质量评价 → 带证据回链的综述结论**。
- 判断对错只看三件事：每一篇纳入/排除都有可查理由；每条论断能回链到具体证据（locator + 逐字 quote）；换人复跑能得出基本一致的纳入集与结论。
- 先定标准、后做筛选；筛选途中不得悄悄放宽或收紧标准。标准变动必须记入 `change_log` 并重跑受影响的记录。
- 证据 id 确定：`ev-` + 对 `locator` 取 SHA-1 前 12 位（`scripts/main.py` 内 `resultlib.evidence_id`）。Agent 可在 `evidence_cards.jsonl` 里显式写 `id`，`synthesis.conclusions[].evidence_ids` 必须引用同一 id；脚本自检会校验未命中。

## 1. P0 骨架（0–3 分钟）

- 输入：`/input/case.json`、`/input/task.md`、材料清单。
- 动作：`python -B scripts/main.py skeleton` 落盘一版合法 `result.json`（`status=partial`）+ `run_state.json`。
- 之后只做增量改进，任何异常保留已落盘 partial，禁止清空重建。

## 2. P1 问题与协议（3–12 分钟）

输出 `/app/work/protocol.json`：

```json
{
  "review_question": "用一句话写清研究问题（对象/干预/结局/范围）",
  "subquestions": [
    {"id": "sq1", "question": "……", "depends_on": [], "answer": "……"}
  ],
  "search_strategy": {
    "query_blocks": [
      {"id": "q1", "database": "PubMed", "field": "ti,ab",
       "query": "(intervention[tiab]) AND (outcome[tiab])",
       "intent": "命中核心干预×结局文献",
       "time_limit": "2010-01-01/2025-12-31", "language_limit": "en,zh",
       "run_status": "run", "hits": null, "run_date": null}
    ]
  },
  "inclusion_criteria": [{"id": "I1", "criterion": "……", "priority": 1}],
  "exclusion_criteria": [{"id": "E1", "criterion": "……", "priority": 1}]
}
```

### 检索式构造规则（防退化）

- 每个 `query_blocks` 必须含 `database`、`field`、`query`、`intent`、`time_limit`、`language_limit`；`query` 须是布尔逻辑 + 同义词/受控词的组合，禁止退化成单个关键词串。
- 由研究问题拆出要素（PICO 或对象/干预/结局/研究类型），每个要素一组同义词；同一概念多种表述并列为 OR，概念间用 AND。
- 检索式原文、检索日期、命中数、数据库与字段必须落盘，供他人复跑。数据库不可用/超时 → `run_status=not_run`、`hits=null`，记 `degradations`，不伪造命中数。

## 3. 纳排标准模板（先于筛选落盘）

条款必须**可判定**（二元，不靠“感觉”），并给优先级与冲突处理。推荐要素：

| 要素 | 示例条款 | 判定 |
|---|---|---|
| 研究类型 | 随机对照试验 / 队列 / 病例对照 / 系统综述 | 类型不符即排除 |
| 对象/人群 | 成人 / 某疾病人群 / 排除动物或体外 | 对象不符即排除 |
| 结局 | 报告了指定主要结局 | 未报告即排除或标 `not_reported` |
| 时间范围 | 2010 年及以后 | 范围外排除 |
| 语言 | 中英文全文 | 其他语言排除或标 `abstract_only` |
| 可得全文 | 有全文或可信摘要 | 仅题录标 `abstract_only` |

优先级规则示例：**类型/对象硬条款 > 时间/语言 > 全文可得性**；冲突时硬条款优先，冲突过程写入 `change_log`。每条 exclude 必须写 `reason` + `criterion_id`（引用 I/E 编号）。

## 4. P2 两轮筛选（12–30 分钟）

输出 `/app/work/screening.jsonl`，每条记录一行 JSON：

```json
{"record_id": "r1", "title": "……", "source_locator": "materials/paper1.pdf",
 "stage": "title_abstract", "decision": "exclude",
 "reason": "非目标人群", "criterion_id": "E2", "judge": "skill-agent",
 "duplicate_of": null, "notes": "……"}
```

- 两轮：`stage=title_abstract`（题录级）与 `stage=full_text`（全文级），逐条 `decision ∈ {include, exclude}`。
- 去重记 `duplicate_of` 指向保留记录的 `record_id`。
- 计数可重算：`finalize` 用 `compute_prisma` 从记录重算 PRISMA，不信任手工填的数字。

## 5. P3 质量评价与证据抽取（30–42 分钟）

### 质量评价 `/app/work/quality_appraisal.jsonl`（每纳入研究一行）

```json
{"record_id": "r1",
 "dimensions": [
   {"dimension": "随机化/分配隐藏", "judgment": "adequate|inadequate|unclear",
    "basis_locator": "materials/paper1.pdf#p3", "quote": "……逐字……"},
   {"dimension": "结局报告完整性", "judgment": "adequate", "basis_locator": "……", "quote": "……"}
 ],
 "overall_grade": "high|moderate|low|critical",
 "grade_rationale": "……",
 "impact_on_conclusion": "该等级如何影响后续结论"}
```

可复用量表维度（按研究类型取舍）：分配隐藏、盲法、失访/缺失数据、结局报告完整性、基线可比性、样本量与精度。每条维度必须给 `basis_locator + quote`，禁止只写“质量较高”。

### 证据抽取 `/app/work/evidence_cards.jsonl`（每行一条证据）

```json
{"record_id": "r1", "claim": "干预使风险下降 20%",
 "locator": "materials/paper1.pdf#p4", "quote": "……逐字……",
 "strength": "fact|inference|not_reported", "id": "ev-……（可省略，脚本按 locator 派生）"}
```

- `quote` 必须逐字取自材料原文；`strength=fact` 表示材料直接陈述，`inference` 表示合理推断，`not_reported` 表示材料未提供（此类不应作为结论依据）。
- 只有摘要无全文 → 该篇标 `abstract_only`，结论降为 `suggestive`。

## 6. P4 综合与收尾（42–56 分钟）

输出 `/app/work/synthesis.json`：

```json
{
  "conclusions": [
    {"id": "c1", "statement": "……", "evidence_ids": ["ev-……"], "strength": "supported|suggestive"}
  ],
  "conflicts": [
    {"id": "x1", "description": "……", "source_a": "r1", "source_b": "r2",
     "resolution": "undetermined|resolved"}
  ],
  "gaps": [{"id": "g1", "gap": "……", "located_at": "具体缺口位置"}],
  "bias": [], "undetermined": []
}
```

- 每条结论 `evidence_ids` 必须命中 `evidence_map`；结论不越过证据范围。
- 冲突逐条并列两个来源；无法裁定标 `undetermined`，不强行取一方。
- 空白落位到具体缺口（人群/结局/时间/地区/方法），不写空话。

随后：`finalize` 组装并重算 PRISMA → `selfcheck` 修复问题、如实定 `status`。

## 7. 断点续跑与确定性

- `run_state.json` 记录 case_id、contract、inventory、started_at；重复运行先读它，继续未完成阶段，不从头重写。
- 证据 id 由 locator 派生、字段顺序固定、`json.dump(sort_keys=True)`，同一输入两次运行的结构一致；`change_log` 时间戳记录运行时刻，属预期差异。
- 模型实质内容只写 `/app/work/`，由脚本确定性组装，避免手改 JSON 出错。

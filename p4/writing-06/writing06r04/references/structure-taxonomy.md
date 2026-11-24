# 六维结构诊断分类法与规则

本文件是可复用规则：定义六个诊断维度、每维的问题类型、严重度/置信度/采纳状态、可执行编辑动作类型，以及 result.json 的完整 JSON Schema。诊断时严格按此执行，不虚构缺陷。

## 1. 六个维度与问题类型

### 维度1 研究问题与贡献主张对齐

检查：研究问题是否明确提出、是否贯穿全文、贡献主张是否与实际完成的工作一致。

- RQ_未明确：研究问题未清楚陈述或藏在细节里。
- RQ_未贯穿：问题在引言提出后，方法与结果未回扣同一问题。
- 贡献_超出工作：贡献主张超出实际完成的工作（如声称"首次"但证据不足）。
- 贡献_与工作不一致：问题与方法各说各话，贡献主张与方法/结果错位。
- 贡献_未声明：有实质工作却未提炼成贡献主张。

### 维度2 章节结构与叙事顺序

检查：章节顺序、篇幅分配、信息释放顺序是否服务于论证。

- 铺垫_后置：该前置的背景/定义/假设放在结果之后。
- 细节_前置：该后移的细节（实现、参数）在论证建立前插入，打断主线。
- 应合并：两章/节讲同一件事，功能重叠。
- 应拆分：一章混装多个不同功能（如方法+结果混排）。
- 篇幅失衡：关键论证章节过薄、铺垫章节过厚。
- 信息顺序不当：结论需要的设定尚未引入就被引用。

### 维度3 论证链与论据对应

检查：逐条论断是否有对应论据，找出断裂、跳跃、循环、论据不足。

- 论证断裂：论断无任何支撑论据（claim 悬空）。
- 逻辑跳跃：论据不直接推出论断，中间缺环节。
- 循环论证：用待证论断自身或其变体作论据。
- 论据不足：论据存在但强度/样本/条件不足以支撑论断。
- 论据错配：引用的图/表/文献与论断内容不匹配。

### 维度4 段落关系

检查：段落间衔接与逻辑推进。

- 缺过渡：相邻段落主题切换无衔接，读者无法跟踪逻辑。
- 逻辑跳跃：段落间存在隐含前提未交代。
- 段落功能不清：一段混装多个论点或一段无主题句。

### 维度5 重复内容

检查：同义重复与跨章节重复表述。

- 同义重复：同一意思在相邻段落换措辞重复。
- 跨章节重复：同一内容在摘要/引言/结果/结论多处复制。
- 冗余_应合并或删除：重复内容占位，建议合并或删除并保留一处权威位置。

### 维度6 结论边界与表述尺度

检查：结论是否超出数据与实验条件，是否遗漏限制，是否把相关当因果。

- 超出数据范围：结论强度超出样本、条件、指标允许的范围。
- 遗漏限制：未说明方法/样本/测量层面的限制。
- 相关作因果：把相关性表述为因果。
- 过度概括：从单一条件/学科/物种外推到普遍结论。

## 2. 严重度（severity）

- critical：影响结论成立或论证主线断裂（如核心 claim 无证据、结论超出数据）。
- major：影响局部论证或章节连贯，但不推翻主线。
- minor：表述尺度、衔接、重复等可快速修复的问题。

## 3. 置信度（confidence）

- high：有逐字引用支撑，问题判定无歧义。
- medium：有引用支撑，但对问题性质存在解释空间。
- low：证据间接，需人工确认（同时写入 pending_human）。

## 4. 采纳状态（acceptance，供作者复核）

每条 issue 预留作者复核结论：accept（采纳）/ partial（部分采纳）/ reject（驳回）。

## 5. 可执行编辑动作（edit_action.action_type）

- move：移动到指定章节/段落。
- merge：与指定段落合并。
- split：从指定位置拆分为两段/两节。
- narrow：收窄论断，限定到数据/条件允许范围。
- add_transition：补充过渡句。
- delete：删除重复/冗余内容。
- rewrite：改写（保留事实，只改结构）。
- add_limitation：补充限制说明。
- reorder：调整顺序。
- restate：在指定位置重述研究问题以贯穿全文。

每条 edit_action 必须含：action_type、target_location（移动/合并/拆分的目标位置）、instruction（可执行指令）、可选 suggested_revision（最小修改示意，非另起改写稿）。

## 6. 反虚构规则（正确性硬要求）

1. 只报告有逐字证据（quote + locator）支撑的结构问题。
2. 某维未发现真实问题：dimension_coverage 标 status="未见结构问题" 并给依据（如"全稿 RQ 在引言、方法、讨论三处一致回扣"），不强行写 issue。
3. 证据不足：dimension_coverage 标 status="证据不足"，不写具体 issue，写入 uncertainty。
4. 不把风格偏好（如用词、句式）当结构错误；结构错误必须指向"论证/顺序/链条/边界"层面。
5. 不因材料缺失而编造缺陷；缺失处写 limitations / pending_human。
6. 每条 claim 必须有 locator（文件+章节/段落/图表编号）与逐字 quote。

## 7. 论点—论据映射规则

对稿件正文逐条抽取论断（claim），每条 claim 记录：

- claim_id、claim（论断原文或压缩重述）、claim_location（locator）。
- evidence_refs：支撑论据的来源列表（图表编号、数据、文献、方法）。
- strength：sufficient（论据足够）/ insufficient（论据不足）/ missing（无论据）/ circular（循环）/ leap（跳跃）。
- verdict：该论断是否成立的判定与理由。

抽取时注意：数字、术语、引用、图表与关键论断都要进入来源映射；不新增材料外事实。

## 8. 结果 JSON Schema（默认契约）

```json
{
  "case_id": "string",
  "status": "in_progress|complete|partial|failed",
  "diagnosis": {
    "summary": {"manuscript_identity": "string|null", "overall_assessment": "string|null",
                 "issue_counts": {"critical": 0, "major": 0, "minor": 0}},
    "issues": [{
      "issue_id": "I01",
      "dimension": "六维之一",
      "severity": "critical|major|minor",
      "location": {"section": "string", "paragraph": "string|null", "sentence_quote": "string|null"},
      "problem_type": "string",
      "problem_description": "string",
      "evidence": {"locator": "string", "quote": "string"},
      "edit_action": {"action_type": "string", "target_location": "string|null",
                       "instruction": "string", "suggested_revision": "string|null"},
      "confidence": "high|medium|low",
      "acceptance": "accept|partial|reject"
    }],
    "claim_evidence_map": [{"claim_id": "C01", "claim": "string", "claim_location": "string",
                             "evidence_refs": ["..."],
                             "strength": "sufficient|insufficient|missing|circular|leap",
                             "verdict": "string"}],
    "dimension_coverage": {"维度名": {"checked": true,
                                     "status": "有结构问题|未见结构问题|证据不足",
                                     "note": "string"}},
    "edit_plan": [{"priority": 1, "issue_id": "I01", "action": "string"}]
  },
  "source_map": [{"claim_id": "C01", "claim": "string", "locator": "string",
                  "quote": "string", "confidence": "high|medium|low"}],
  "version_boundary": {"diffs": [], "human_confirm": [], "material_gaps": []},
  "artifacts": {"diagnosis_md": "string|null", "preview": "string|null"},
  "sources": [{"claim_id": "C01", "claim": "string", "locator": "string",
               "quote": "string", "confidence": "high|medium|low"}],
  "uncertainty": [{"item": "string", "reason": "string",
                   "status": "pending|not_reported|conflict"}],
  "pending_human": [{"item": "string", "reason": "string"}],
  "limitations": [{"scope": "string", "detail": "string"}],
  "change_log": [{"time": "ISO8601", "action": "string", "status": "string"}],
  "runtime": {"started_at": "ISO8601", "material_count": 0}
}
```

若 case.json 显式提供 output_contract，则按其中键名回填全部键（缺值给 null/[]，不改名、不删未知键）；本 Schema 仅作默认契约与 diagnosis 内部结构参考。

## 9. 边界

- 本 Skill 只做结构与逻辑诊断，不做语言润色、不做全新实验、不新增文献检索填补核心空白。
- 没有真实材料时输出"材料不足"，不用示例填充。
- 相对语言润色类工具的增量：六维问题分类 + 论点—论据映射 + 可执行编辑动作 + 逐字可回溯证据。

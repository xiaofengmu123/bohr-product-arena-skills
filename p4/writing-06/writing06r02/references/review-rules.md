# 六维结构诊断规则（review-rules）

本文件是 paper-structure-review 的判定依据。所有诊断、编辑动作与语气都按本规则执行；
脚本只提供候选锚点，规则与判断由 Agent 依据原文逐条核对后落盘，不凭空生成。

## 0. 通用要求（每条判断的硬格式）

每条结构问题必须写成可复核条目，至少包含：

- `dimension`：六维之一（见下表键名）；
- `issue_type`：该维度下的问题类型（见下）；
- `locator`：`文件#P段号` 或 `文件#P段号S句号`（1 基；例：`paper.md#P12S3`）；
- `quote`：该位置的逐字原文片段（不改写、不省略，必要时以 `…` 标注截断）；
- `evidence`：说明该片段为何构成此结构问题（依据，而非印象）；
- `edit`：对应的一条可执行编辑动作（见"编辑动作分类"）。

区分"结构问题"与"风格偏好"：只有影响论证成立、信息释放顺序、逻辑闭环或结论有效性
的才判为结构问题；措辞、语态、长短句属风格偏好，除非其直接造成逻辑跳跃，否则不写入
诊断，避免"放之四海而皆准"的写作原则。

## 1. research_question —— 研究问题与贡献主张对齐

检查点：研究问题是否被清楚提出、是否贯穿全文；贡献主张是否与实际完成工作一致。

- `question_absent`：引言未见可验证的研究问题。
- `question_vague`：问题表述为"本研究探讨/分析了 X"等不可验证措辞。
- `contribution_mismatch`：结论声称完成的工作超出方法/结果实际做到的范围。
- `question_scope_drift`：方法与结果回答的问题与引言提出的问题不一致（各说各话）。

## 2. section_structure —— 章节结构与叙事顺序

检查点：章节顺序、篇幅分配与信息释放顺序是否服务论证。

- `missing_section`：论证需要但缺失的章节（如无方法、无局限）。
- `misplaced_detail`：应前置的铺垫后置，或应后置的细节前置。
- `order_break`：章节顺序打断论证链（结果先于方法、结论先于结果）。
- `disproportionate_section`：篇幅分配与贡献权重明显失衡。

## 3. argument_chain —— 论证链与论据对应

检查点：逐条抽取论断与支撑论据，找断裂、跳跃、循环论证与论据强度不足。

- `unsupported_claim`：论断无对应论据（数据/文献/推导）。
- `logical_leap`：中间步骤缺失，从前提直接跳到结论。
- `circular_argument`：论据与论断互为前提。
- `weak_evidence`：论据强度不足以支撑论断的措辞强度（如个案推断群体规律）。

## 4. paragraph_relations —— 段落关系

检查点：段间过渡、逻辑衔接、段落功能单一。

- `missing_transition`：相邻段落缺少过渡，出现逻辑跳跃。
- `topic_mix`：一个段落混入多个论点。
- `paragraph_split_merge`：该拆分的长段、或该合并的同义段。

## 5. redundancy —— 重复内容

检查点：同义重复与跨章节重复表述；区分"有意呼应"与"无意冗余"。

- `duplicate_sentence`：同段或近段逐字/近逐字重复。
- `cross_section_repetition`：同一表述在方法、结果、讨论、结论多处无差别复述。
- `near_duplicate`：换词但信息完全相同的重复。
- 摘要/结论对结果的简短重申属有意呼应，不判为问题；超过一句的复述才判冗余。

## 6. conclusion_boundary —— 结论边界与表述尺度

检查点：结论是否超出数据与实验条件允许范围，是否遗漏限制，是否把相关性写成因果。

- `overgeneralization`：把特定样本/条件的结论推广到未覆盖的总体。
- `missing_limitation`：遗漏必要的方法/样本/测量/统计限制。
- `correlation_as_causation`：把相关表述为因果（"相关/关联"写成"导致/引起/因果"）。

## 编辑动作分类（edit actions）

| 动作 | 含义 | 必须写明的字段 |
|---|---|---|
| `move` | 移动到哪一位置 | target_locator, dest |
| `merge` | 与哪段合并 | target_locator, merge_with |
| `split` | 从哪里拆开、拆成几段 | target_locator, split_after |
| `narrow` | 哪句论断需要收窄到何范围 | target_locator, narrower_claim |
| `add_transition` | 在哪两段间补衔接句 | between, suggested_link |
| `reorder` | 重排到何顺序 | before/after |
| `add_limitation` | 补哪类限制 | limitation_type |
| `rewrite_claim` | 只改论断措辞强度，不改事实 | target_locator, new_wording |

每条 edit 都要给出 `priority`（high/medium/low）与 `acceptability`（adopt/partial/reject），
并说明作者可据此判断采纳、部分采纳还是驳回的复核依据。

## 论断强度语气映射（按证据强度选动词）

- 实测/直接观察结果 → "显示、表明"；
- 相关性 → "相关、关联"，不写"导致、引起、因果"；
- 有限样本/单中心 → 注明样本与条件范围，不推断总体；
- 机制推测 → 显式标注"推测/可能/提示"，不写成事实；
- 局限 → 落到方法/样本/测量/统计层面，不用"存在不足"的空泛表述。

## 优先级（priority）

- `high`：影响结论有效性或论证闭环，作者必须处理；
- `medium`：影响论证流畅或信息释放，建议处理；
- `low`：结构风格，可自行决定。

## acceptability（作者复核结果）

- `adopt`：原文位置、类型、依据都可复核，作者采纳；
- `partial`：判断方向成立但动作需调整；
- `reject`：判断依据不成立（如把风格偏好误判为结构问题）。

## 可复用模板（他人可直接套用）

### A. 结构问题标注模板（单条 issue，写入 result.json.diagnosis.dimensions[维].issues[]）

```json
{
  "dimension": "argument_chain",
  "issue_type": "unsupported_claim",
  "locator": "paper.md#P12S3",
  "quote": "……（该句逐字原文）",
  "evidence": "该论断未附数据/文献/推导支撑，且与前文论据之间缺少衔接。",
  "edit": {
    "type": "narrow",
    "target_locator": "paper.md#P12S3",
    "narrower_claim": "把论断收窄到当前样本与实验条件范围内。",
    "priority": "high",
    "acceptability": "adopt"
  }
}
```

### B. 论点—论据映射表（写入 result.json.source_map，Markdown 渲染）

| claim_id | 论断（逐字 quote） | 论断 locator | 论据类型 | 论据 locator | 论据强度 | 是否闭环 |
|---|---|---|---|---|---|---|
| C1 | 逐字原文 | paper.md#P8S1 | 数据/文献/推导 | table1 / ref[3] | 强/中/弱 | 是/否/断裂 |

- 论据类型只填 `数据`、`文献`、`推导`、`观察` 之一；无据填 `无`，并标 `断裂`。
- "是否闭环"：研究问题→方法→结果→结论能否逐环节对上；对不上标 `断裂` 或 `跳跃` 并说明缺哪一步。

### C. 结论边界比对表（用于 conclusion_boundary 维，写入 diagnosis）

| 结论句 | locator | 支持的数据/实验条件 | 结论声称范围 | 是否越界 | 建议收窄为 |
|---|---|---|---|---|---|
| 逐字原文 | paper.md#P20S2 | 单中心 n=40 | 全体患者 | 是 | 该中心、该条件样本 |

### D. 最小调用示例（示意输出，非正式案例结果）

```bash
cd /skills/weekly-skill
python scripts/main.py            # 读 /input，落盘 partial 骨架，生成 evidence.json
python scripts/selftest.py        # 自检，退出码 0
```

示意 `result.json` 片段（仅作接口示例，不含任何正式案例结论）：

```json
{
  "case_id": "demo-case",
  "status": "partial",
  "diagnosis": {
    "summary": "（示意）已完成六维诊断，结论边界维发现 1 处越界。",
    "dimensions": {
      "conclusion_boundary": {
        "status": "issue",
        "issues": [
          {"issue_type": "overgeneralization", "locator": "paper.md#P20S2",
           "quote": "……", "evidence": "……", "edit": {"type": "narrow"}}
        ]
      }
    }
  },
  "sources": [{"claim_id": "C1", "claim": "……", "locator": "paper.md#P20S2",
               "quote": "……", "source_ref": "paper.md#P20S2", "confidence": "high"}],
  "uncertainty": [],
  "pending_human": [],
  "change_log": [{"time": "2026-01-01T00:00:00+08:00", "action": "skeleton_written",
                  "status": "in_progress"}]
}
```


# result.json 字段定义（output_contract）

`/app/output/result.json` 是 Skill 的主结果文件。若 `/input/case.json` 给出 `output_contract`，以其字段为准并**原样回填全部键**（缺值给 `null`/`[]`，不改名、不删未知键）；否则使用本文定义的默认契约。包裹字段（case_id / status / sources / uncertainty / pending_human / change_log）无论哪种情况都必须存在。

## 包裹字段（必填）

| 字段 | 类型 | 说明 | 空值 |
|-|-|-|-|
| `case_id` | string | 案例标识，优先 `case.json.id`，其次 `armiId` | 不得为空；缺失时用 `"unknown"` |
| `status` | string | `complete` / `partial` / `failed` / `insufficient_evidence` / `input_error`，如实标注 | — |
| `sources` | array | 实际读取的来源清单，每项含 `path`、`type`、`read`、`note` | `[]` |
| `uncertainty` | array | 无法判断、材料缺失、证据不足等逐条说明 | `[]` |
| `pending_human` | array | 需要人工确认/补录的事项，不得编造补齐 | `[]` |
| `change_log` | array | 增量记录，每项含 `ts`（ISO 时间戳）、`action`、`scope` | `[]` |

## 内容字段（默认契约）

| 字段 | 类型 | 说明 |
|-|-|-|
| `report_goal` | object | 可检查的报告目标：目标、听众、时长、页数、结论强度；例如 `{goal, audience_kind, minutes, slides, claim_floor}` |
| `audience` | object | 听众先验知识、关注点、可能质疑，及由此决定的背景比例/术语深度/证据深度 |
| `narrative` | array | 页面序列与取舍理由（非按论文目录搬运）；每项含 `seq`、`page_title`、`why` |
| `pages` | array | 页级结构，见下表 |
| `editable_deck` | object | 可编辑演示文件：`path`、`format`（pptx/odp/html）、`template`（所用模板） |
| `evidence_map` | array | 每页主张 → 来源定位，见下表 |
| `qa_prep` | array | 预判追问与口径一致回答，见下表 |
| `rendering` | object | 源文件、导出件（`pdf`/`png` 路径）、字体、模板、修改记录 |

## pages 元素

| 键 | 类型 | 说明 |
|-|-|-|
| `page_index` | int | 页序（从 1 起） |
| `title` | string | 页标题 |
| `single_claim` | string | 该页唯一主张（一句话可讲结论） |
| `evidence_strength` | string | `high` / `medium` / `low`，与证据匹配：结果对比 claims 用 high，机制推测用 low |
| `figures` | array | 图表引用（图/表编号或文件名） |
| `speaker_notes` | string | 讲稿备注，与页面结论对应，不与页面文字重复 |
| `source_refs` | array | 指向 evidence_map 的定位（文件/章节/页码/图表） |

## evidence_map 元素

| 键 | 类型 | 说明 |
|-|-|-|
| `page_index` | int | 对应页 |
| `locator` | string | 来源定位，如 `materials/paper.pdf#p3`、`图2`、`表1`、`第4.2节` |
| `quote` | string | 逐字片段（≤120 字），未改写事实 |
| `source_path` | string | 材料文件相对路径 |
| `strength` | string | high/medium/low |

关键结论必须带 `locator` + `quote`；无定位时 `evidence_strength=low` 并记入 `pending_human`。冲突信息不隐藏：同一指标来源不一致时两条都记录并标 `source_conflict`。

## qa_prep 元素

| 键 | 类型 | 说明 |
|-|-|-|
| `question_type` | string | `mechanism` / `data` / `control` / `limitation`（机制、数据、对照、局限四类） |
| `question` | string | 预判追问 |
| `answer_points` | array | 口径一致的回答要点 |
| `cite_pages` | array | 可引用的页面序号 |
| `backup_page` | string/null | 备用页（可选） |

## rendering 元素

| 键 | 类型 | 说明 |
|-|-|-|
| `source_files` | array | 源文件相对路径 |
| `pdf` | string/null | 渲染 PDF 预览路径 |
| `png` | string/null | 渲染 PNG 预览路径 |
| `fonts` | array | 所用字体及缺失情况 |
| `template` | string/null | 模板名或路径 |
| `change_records` | array | 导出修正次数与来源 |

## 状态语义

- `complete`：主产物 + 可编辑文件 + 渲染预览齐全且自检无机器 fail 项。
- `partial`：骨架已落盘，部分内容/产物缺失，但结果合法可读。
- `failed`：执行失败但保留了部分产物与说明。
- `insufficient_evidence`：材料缺失/无法读取，无法支撑结论，如实列出缺口。
- `input_error`：输入契约无法解析。

`status` 不得编造：缺失就如实标注，不以 `complete` 冒充。

## 附录 A：页级金标准（可复用）

判断一页是否“可直接上台”，逐项核对；不满足即返工，返工原因记入 `rendering.change_records`：

1. **一条主张**：页面只有一个 `single_claim`，能用一句话复述；出现“并且/此外/值得注意的是”等多主语时拆页。
2. **主张可定位**：`single_claim` 的每个数字、术语、对比结论都能在 `source_refs` 指到具体图/表/章节/页码。
3. **强度匹配**：观察性对比结果 → high；需要机制解释或外推 → low；中间态 → medium。强度由“证据能支撑到什么程度”决定，不由语气决定。
4. **图表服务主张**：`figures` 只列直接支撑 `single_claim` 的图/表，多余图移入 `narrative` 的取舍理由或备用页。
5. **讲稿不重复正文**：`speaker_notes` 是“怎么说”，不是把页面文字再念一遍；应给出过渡句、口头解释、该页要落到的一句话。
6. **数字原样**：页码上的数值与 `quote` 逐字一致，不四舍五入、不改写单位、不合并两个来源的数字。

## 附录 B：叙事模板（问题—方法—结果—结论 + 取舍决策）

`narrative` 每项建议结构（字段可增删，但 `why` 必填，用于他人复核取舍）：

```json
{
  "seq": 1,
  "page_title": "问题：为什么必须现在解决 X",
  "role": "problem|method|result|conclusion|bridge",
  "why": "先立问题而非先给背景，因为听众已知背景，先讲缺口能锁定其关注点",
  "kept": ["研究问题", "核心缺口"],
  "dropped": ["文献综述全量背景", "方法细节（后置）"],
  "time_sec": 90
}
```

取舍规则（写入 `why`，不空泛）：

- 背景铺垫比例由 `audience.prior` 决定：先验高 → 只留一句话缺口；先验低 → 保留必要术语解释。
- 方法细节只保留“影响结论可信度”的部分；实现细节移到备用页或讲稿口头带过。
- 结果页按“结论强度”排序，最强证据最先上页；弱证据/机制推测置后并标 low。

## 附录 C：复用提示

- 本契约可直接作为新案例的 `output_contract` 模板复制；字段含义稳定，未知键一律保留。
- `pages`/`evidence_map`/`qa_prep` 的键名与 `selftest.py` 的机器检查项一致，改键名会触发 fail 项。
- 交付他人前先跑 `python3 scripts/selftest.py --strict`，确认无机器 fail 项，再人工核对 H01–H03。

# 方法：意见拆解、逐条回应、锚点定位与语气分级

本文件定义 Skill 运行时使用的分类法与字段语义，供 Agent 与复核人对照。所有枚举值必须与 `revision-ledger.json` 一致。

## 1. 意见拆解（答得全）
每条意见拆为五元组：`comment_id` + `comment_type` + `severity` + `sub_requirements[]` + 关系。
- 类型 `comment_type`：`method`（方法/模型/分析/统计）、`data`（数据/实验/结果/图表/可复现）、`writing`（写作/结构/表述/论证）、`format`（引用/格式/规范/编号）、`ethics`（伦理/同意/利益冲突/数据可用）。
- 严重度 `severity`：`major`（影响结论或需补实验）、`minor`（局部改进）、`editorial`（文字/格式）。
- 子要求 `sub_requirements[]`：一条意见中所有可独立执行的诉求，逐条拆出；"请补实验并报告效应量"拆为「补实验」「报告效应量」两项，避免只答一半。
- 关系：用 `changes[].action` 与 `evidence` 关联；相互矛盾的意见在 `version_boundary.anomalies` 标注 `source_conflict`。

## 2. 逐条回应与语气（改得准的前提）
- 决策 `decision`：`accept`（采纳）、`partial`（部分采纳）、`reject`（不采纳）。
- 语气 `tone` 四档，与决策可组合：
  - `accept`：接受——致谢 + 确认 + 落到具体动作，不空话。
  - `clarify`：澄清——指出误解，重述原意，通常不改或微调。
  - `rebut`：礼貌反驳——先承认审稿人关切，再给反证与依据，respectful disagreement。
  - `appeal`：申诉——正式升级，仅在有强证据且与编辑沟通时使用。
- 硬规则：不冒犯性表达、不过度承诺（不承诺未做的实验）、不无原则顺从（不假装同意而实际未改）。

## 3. 修改定位（改得准 / 对得上）
每处改动四元组：`section`（章节）+ `before`（原文片段）+ `after`（修订后片段）+ `anchor`（8–20 字唯一短引文）。
- `anchor` 须唯一且不依赖行号（行号随编辑漂移），`check_revision.py` 用归一化子串匹配验证 `after` 或 `anchor` 落在当前修订稿。
- `section` 用 `scripts/main.py` 生成的章节 slug（见 `/app/work/sections.json`）；无标题段落用 `preamble`。
- 多轮返修：`version_id` 递增，上一轮承诺在台账中保留，回复信只引用当前版本锚点。

## 4. 证据补充（证得住）
- `evidence.needed`：`method`/`data` 类意见默认 true；写作/格式类按需。
- `evidence.status`：`done`（已在稿件/附录给出）、`partial`（部分给出）、`cannot_do`（无法补做）、`planned`（已列入计划）。
- 无法补做必填 `evidence.cannot_do_reason`（如：无原始数据、伦理限制、时间/资源不可行），并给出替代说明；不得虚构实验或数据。
- `done` 必填 `evidence.source`（材料路径或稿件定位）。

## 5. 版本一致性（对得上）
- 台账 `revision-ledger.json` 是单点真相；回复信逐条引用 `comment_id`，修改稿逐处对应 `changes[]`。
- `result.json` 的 `version_boundary.changes` 必须与台账改动一一对应（`check_revision.py` 校验数量一致）。
- 冲突信息不隐藏：同一指标两来源不一致时两处都记录并标 `source_conflict`。

## 6. 事实溯源
每条数字/术语/引用/关键论断写入 `fact_source_mapping`，`quote` 为逐字引文，`source_ref` 为可复核定位（如 `materials/manuscript-baseline.md#L12-14`），`status` ∈ `traced`（可溯源）|`revised`（本次修订）|`assumption`（假设）|`unverified`（未验证）。不新增材料外事实，不虚构 DOI/引用/数值。

## 7. 异常与边界处置
| 场景 | 识别信号 | 处置 |
|-|-|-|
| 意见互相冲突 | 两条意见要求相反动作 | 两条都记录并标 `source_conflict`，正文取其一并说明理由，另一条列入 `pending_confirmation` |
| 意见本身有误 | 审稿意见引用的事实与材料/文献不符 | 语气用 `clarify` 或 `rebut`，给出反证定位，`status=rejected`，不假装接受 |
| 稿件缺章节 | 意见指向基线中不存在的章节 | 写入 `material_gaps`，回复信说明"基线未含该章节"，可执行部分仍完成，不硬填 |
| 无法补实验 | 无原始数据/伦理限制/资源不可行 | `evidence.status=cannot_do` + `cannot_do_reason` + 替代说明，`uncertainty` 记一项，不虚构数据 |
| 版本不匹配 | 意见/回复引用的行号在当前稿不存在 | 改用文本 `anchor` 而非行号，重跑 `check_revision.py` 校验落地 |
| 权限不足/PDF 无法解析 | 读取失败或平台工具不可用 | 记 `degradations`，相关条目标 `unverified`，`status=partial`，不编造软件状态 |
| 多轮返修承诺漂移 | 上一轮承诺未在本轮兑现 | `version_id` 递增，保留历史 entry，本轮标注承接或放弃及原因 |

## 8. 决策速查
- `accept`：意见合理且可执行 → 真实改入稿件，`changes[]` 记 before/after/anchor。
- `partial`：部分合理 → 只做合理部分，说明未做部分与理由。
- `reject`：意见有误或不可行 → 用 `clarify`/`rebut` 给依据，不空口拒绝。
- 语气不与决策绑定，但 `appeal` 仅在需编辑复核时使用。
- 完成前四问门禁必须全过：答得全（coverage）、改得准（locality）、证得住（evidence）、对得上（version_boundary）。


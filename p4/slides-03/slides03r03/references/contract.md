# result.json 输出契约字段定义（contract version 2.0.0）

本文件定义 Skill 交付物 `/app/output/result.json` 的字段语义。若 `/input/case.json`
携带 `output_contract`，该对象**原样回填**（含未知键，缺值用 `null`/`[]` 占位，不改名、
不删除）；未声明字段按下方默认契约补齐。字段名与取值约定如下。

## 1. 信封字段（Skill 必加）

| 字段 | 类型 | 说明 |
|-|-|-|
| `case_id` | string | 案例标识；优先 `case.json.id`，其次 `armiId`、`case_id`，再次 `task.md` 的 `challenge_id`。 |
| `status` | string | `complete` / `partial` / `failed` / `insufficient_evidence` / `input_error`，必须诚实标注。 |
| `sources` | array | 实际读取的来源清单，每项含 `path`（相对输入目录）、`type`、`read`、`size`，可读时附 `sha256`。 |
| `uncertainty` | array | 无法判断、材料缺失、证据不足等逐条说明；禁止补造。 |
| `pending_human` | array | 需要人工确认或补齐的事项，逐条列出。 |
| `errors` | array | 解析/运行错误记录（可为空数组）。 |
| `degradations` | array | 降级记录（缺图、缺字体、导出失败、超限跳过等）。 |
| `change_log` | array | 增量修改记录；每项含 `step`（确定性序号，如 `1-scaffold`、`2-update`）、`action`、`scope`。为保确定性，不写墙钟时间戳。 |
| `run` | object | `run_id`（内容寻址，对固定案例稳定）+ `contract_version`。 |

## 2. 默认契约字段（case.json 未给 output_contract 时）

| 字段 | 类型 | 说明 |
|-|-|-|
| `report_goal` | object | 可检查目标：本次汇报要达成什么，含听众、时长、页数约束。 |
| `audience` | object | 听众先验知识、关注点、可能质疑；并说明其如何影响页数/术语深度。 |
| `narrative` | object/array | 页面序列与取舍理由；非按论文目录搬运，须写明为何某图不上页、为何调整顺序。 |
| `pages` | array | 页级结构。每页对象含 `page_index`、`title`、`single_claim`（唯一主张）、`evidence_strength`（high/medium/low）、`figures`、`speaker_notes`、`source_refs`。 |
| `editable_deck` | object | 可编辑演示文件：`path`、`format`、`template`。未产出为 `null`。 |
| `evidence_map` | array | 每项含 `locator`（图/表/章节/页码定位）与 `quote`（材料中的逐字引用），未改写事实。 |
| `qa_prep` | array | 预判追问：机制、数据、对照、局限四类，含口径一致回答与可引用页面。 |
| `rendering` | object | 源文件、导出件（PDF/PNG 预览）、字体、模板、修改记录。未渲染为 `null`。 |

## 3. locator 与 quote 约定

- `locator` 采用 `材料相对路径#定位` 形式，如 `materials/paper.pdf#Fig.3`、`materials/paper.md#Results-p2`。
  无法定位时必须如实写 `not_reported`，不得补造页码。
- `quote` 必须是从已读取材料中**逐字**摘录的原文片段（可截断但不得改写）；非逐字内容改放
  `speaker_notes` 或 `narrative`，不要放进 `quote`。
- 同一指标两个来源不一致时，两个来源都记录，并在对应项标 `source_conflict`，不得隐藏冲突。

## 4. evidence_strength 取值

- `high`：原文以图表数据、统计检验或明确声明直接支持的结论。
- `medium`：由多段证据合取的推断，或结果与结论间存在一步推理。
- `low`：机制推测、作者讨论、单一孤立证据或材料缺失下的保守表述。

## 5. 确定性约定

同一固定案例重复运行时，`result.json` 必须逐字节一致：

1. 序列化用 `main.py` 的 canonical 形式（`sort_keys`、紧凑分隔符、`ensure_ascii=False`、禁用 NaN）。
2. 字段插入顺序无关（canonical 排序）；`sources`、`materials` 一律按路径排序。
3. `run_id` 由 `case_id` 与各来源 `sha256` 计算，不依赖墙钟时间。
4. `change_log` 用确定性 `step` 序号，不写时间戳。
5. 任何布局/随机抽样必须固定种子并在 `rendering` 记录种子值。

交付前运行 `python3 scripts/selftest.py` 验证上述约定。

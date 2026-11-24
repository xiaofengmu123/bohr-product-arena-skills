# 降级、核验与定位约定

本文件规定材料缺失、工具不可用、引用无法核实、输入超长、运行失败时的处理方式，
以及证据定位（locator）与核验规则，并给出 `result.json` 字段级规范。原则只有一条：
**如实标注，不编造**。

## 1. 状态语义（result.json.status）

| status | 含义 |
|-|-|
| `in_progress` | 已落盘骨架/中间稿，仍在继续填充 |
| `partial` | 已交付部分产物，存在缺口（已在 uncertainty/version_boundaries 标注） |
| `complete` | 已交付全部必需产物并通过自检 |
| `failed` | 运行失败，仍已落盘 result.json 与中间产物 |
| `insufficient_evidence` | 材料不足，无法写作，只交付缺口清单 |
| `input_error` | 输入损坏（坏 JSON/坏编码/缺 case_id），已降级 |

任何情况下 result.json 都必须落盘；`failed`/`insufficient_evidence`/`input_error`
也要给出 `errors`、`uncertainty` 与已完成中间产物路径。

## 2. 降级策略（禁止编造）

| 场景 | 处置 |
|-|-|
| 材料缺失 / 字段不明 | 在 `uncertainty` 或 `version_boundaries.gaps` 标注“缺失/无法判断”，字段留空或 `null`，不补造数值、文献、作者、政策口径 |
| 引用无法核实 | 保留原文位置并标 `status=unverified`，不伪造 DOI/URL |
| PDF/图片材料 | 平台用 pdf-text-tool / ocr-tool 解析后再写；解析失败时标 `not_reported`，不按文件名猜内容 |
| 图表读数不清（刻度/误差棒含义不明） | 标 `unverified` 或 `pending_human`，不估读 |
| 软件/工具不可用（无 office 渲染等） | 如实写 `limitations`，交付纯文本/结构化产物，不假装已渲染 |
| 输入超长/单文件 > 2MB | 脚本自动跳过或截断并在 `degradations` 说明“已截断/未覆盖” |
| 写盘失败 | 降级到最小合法产物，进程不崩溃 |
| 运行失败 | `status=failed`，`change_log` 记录失败原因与已完成中间产物 |

## 3. 定位（locator）约定

`scripts/main.py quote --file <材料> --locator <定位>` 用于提取逐字 quote。
支持的定位写法：

| 写法 | 含义 |
|-|-|
| `L12` | 第 12 行 |
| `L12-15` / `L12-L15` | 第 12–15 行（含） |
| `P3` | 第 3 段（按空行分段） |
| `Fig 2` / `Figure 2a` / `Table 3` | 匹配该图/表编号所在图注块 |
| `图 2` / `表 3` | 中文图/表编号 |
| 空 / 不写 | 整文件（截断到 4000 字符） |

推荐写法：`材料相对路径#定位`，例如 `materials/table2.md#L12-L15`、
`figures/fig2.txt#Fig 2`。`facts_sources[].locator` 使用该形式。

## 4. 核验规则

1. **数值核验**：正文每个数值在 `facts_sources` 中有 locator + 逐字 quote，
   且 quote 含该数值、单位、误差棒含义或统计量。
2. **论断核验**：讨论段每个论断标注 `kind`：
   - `observed`：本研究数据直接支持；
   - `statistic`：来自统计输出（附检验名与 p 值）；
   - `reference`：来自具体文献（附可核实引用）；
   - `interpretation`：作者推断/推测，正文须相应措辞（可能/推测）。
3. **一致性核验**：`check` 子命令校验 result.json 结构、output_contract 字段齐全、
   facts_sources 逐条有 locator+quote、全文无过度声明禁词、change_log 条目形状合法。
4. **冲突核验**：同一指标来源不一致时两条都记录并标 `source_conflict`，不静默选一个。

## 5. 未编造纪律

- `status` / `sources` / `uncertainty` / `pending_human` / `change_log` /
  `errors` / `degradations` 必须与真实执行一致。
- 不写任何评测分数、不伪造运行结果、不预置正式案例答案。
- 缺少的内容写“缺失/无法判断”，不写占位假值（除骨架阶段的 `null`/`[]`）。

## 6. result.json 字段级规范

运行时以 `/input/case.json` 的 `output_contract` 为准；下表字段名若与 contract
冲突，以 contract 为准，本表规范通用附加字段。

| 字段 | 类型 | 必需 | 说明 |
|-|-|-|-|
| `case_id` | string/null | 是 | 与 /input/case.json 一致；缺省时骨架写 `null` |
| `deliverable` | object | 是* | `{"file":"manuscript.md","files":[],"format":"markdown","note":null}` |
| `facts_sources` | array | 是* | 证据映射数组，条目见下 |
| `version_boundaries` | object | 是* | `{"changes":[],"human_confirm":[],"gaps":[],"anomalies":[]}` |
| `status` | string | 是 | 六值之一（见第 1 节） |
| `sources` | array | 是 | `{"file":"…","kind":"text|binary","lines":N}` |
| `uncertainty` | array | 是 | `{"item":"…","reason":"…","action":"pending_human|unverified|not_reported"}` |
| `pending_human` | array | 是 | `{"question":"…","why":"…","material":null}` |
| `change_log` | array | 是 | `{"time":"ISO8601","action":"…","detail":"…"}` |
| `degradations` | array | 否 | 降级记录 |
| `errors` | array | 否 | 执行错误记录 |
| `runtime` | object | 否 | `{"started_at":…,"budget_s":900,"budget_requests":64,"tokens_per_response":16384}` |

\* 为 `output_contract` 未声明时的默认字段；声明后以声明字段为准，其余附加字段仍补齐。

**facts_sources 条目**：

```json
{"claim":"正文中的论断或数值句",
 "locator":"table2.md#Table 2 或 fig2.txt#L12-L15",
 "quote":"材料中逐字原文片段（用 scripts/main.py quote 提取）",
 "kind":"observed|statistic|reference|interpretation",
 "status":"verified|unverified|pending_human|not_reported",
 "note":"可选：误差棒含义、检验名、冲突说明等"}
```

**version_boundaries 子字段**：`changes`（`{"from","to","why"}`）、`human_confirm`
（`{"item","reason"}`）、`gaps`（`{"what","impact"}`）、`anomalies`（`{"what","detail"}`）。

**合法性检查**：`python scripts/main.py check --result /app/output/result.json
--summary /app/work/input_summary.json` 校验 JSON 可解析、case_id 非空（除非
insufficient_evidence/input_error）、contract 字段齐全、facts_sources 逐条有
locator+quote、change_log 条目形状、sources/uncertainty/pending_human 为数组、
deliverable 文本无过度声明禁词。

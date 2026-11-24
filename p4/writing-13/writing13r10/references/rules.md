# 规则与判定标准（references/rules.md）

本文是 `scripts/` 之外的事实性规则层，Agent 在运行时逐条遵循；脚本只负责落盘与校验，不替代判断。

## 1. 三层中间表示（IR）

执行中所有中间状态统一写入 `/app/work/workbench.json`，这是唯一事实源；`/app/output/result.json` 是从 workbench 派生的对外结果，二者不得分叉。

| 层 | 字段 | 内容 | 用途 |
|---|---|---|---|
| L1 来源清单 | `source_manifest` | 每个输入文件的路径、大小、SHA256、编码、是否被截断 | 证明“读了哪些材料”，可复核 |
| L2 证据台账 | `evidence_bank` | 每条证据的 claim / kind / source_file / locator / quote / status | 证明“每句话从哪来” |
| L3 要素映射 | `element_map` + `consistency_matrix` | 七要素覆盖 + 目标/内容/路线/指标逐行对齐 | 证明“七要素齐全且对得上” |

## 2. 证据溯源硬规则（查得到）

- 每条关键结论、数字、术语、引用、图表、政策口径必须登记为一条 evidence，且同时给出 `locator`（如 `paper.md#p3`、`fig2`、`table1`）与 `quote`（材料中的**逐字片段**）。
- `quote` 必须取自实际文件，禁止凭记忆转述；找不到原文就写 `not_reported` 并标 `status=unmapped`。
- `status` 取值：`mapped`（已定位）、`unmapped`（材料缺失）、`conflict`（两处来源不一致）、`pending_human`（需人工确认）。
- 冲突不隐藏：同一指标两个来源不一致时，两条都登记并标 `conflict`，在 `version_boundary.anomalies` 说明双方依据，不擅自选一。
- 严格区分 `已完成` 与 `拟开展`：`kind` 用 `prior_result`（已完成）或 `method`/`indicator`（拟开展），不得把拟开展写成已完成。

## 3. 七要素与四道闸门

七要素（`element_map` 必须逐项出现）：立项依据、科学问题、研究目标、研究内容、创新点、技术路线、研究基础与可行性。

四道闸门在**成稿之前**判定（先校验后成稿）：

- 立得住：立项依据含必要性论证（领域现状→瓶颈→前期观察→为何现在必须做），不堆政策口号。
- 说得准：每条创新点写明与代表性已有工作的具体差异、难点、凭什么做得成；禁用“首次/国际领先/填补空白”等无核验修饰。
- 对得上：`consistency_matrix` 的 goal / content / route / indicator 四列逐行对齐，不出现“目标三个、路线两条、指标另说”。
- 查得到：证据闭环（见第 2 节）。

## 4. 体裁、受众、格式（以题面为最高优先级）

- 先读 `/input/task.md` 与 `case.json` 的约束，确定体裁（申请书章节稿/改稿/格式/引用/双语/结构化）、受众、格式、字数、引用风格、语言。
- `output_contract` / `output_schema` / `required_fields` 字段**原样并入** result.json，未知键不删、不改名、缺值给 `null`/`[]`。
- 产物必须是**可直接进入申请书的章节稿 + 要素对照说明**，不是写作建议、提纲或整篇模板套用。
- 可编辑文本给 `.md`/`.tex`/`.docx`；Office/slide/diagram 另附真实渲染的 PDF/PNG 预览并在 `artifact.preview_path` 或 `sources` 注明；缺渲染软件则写 `uncertainty`，保留可编辑中间产物，不伪造预览。

## 5. 不编造红线

- 不编造数字、引用、DOI、卷期页码、图表内容、实验数据、benchmark 分数、运行结果。
- 不把文件名或领域标签当作事实；实际输入文件与原论文才是事实依据。
- 缺数据就写 `not_reported` / `pending_human` / `material_gaps`，不补造。
- 引用文献只能登记材料中**实际出现**的条目；材料外新增文献必须标 `pending_human` 并说明需要人工核实。
- 不预写正式测试答案或白板；平台提供对照。

## 6. 冲突与复现

- 冲突：记录双方依据（各带 locator + quote），不擅自改分、不擅自选一。
- 复现：`sources` 含 SHA256，`change_log` 记录每次修改（时间/动作/原因/影响）；同一输入重复运行应产出口径一致的 result.json（脚本确定性、无随机）。

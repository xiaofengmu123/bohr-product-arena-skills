# 方法规则（references/method.md）

本文件是 `SKILL.md` 第 4 节执行流程的可操作细则，供 Agent 在每一步对照执行。所有结论都要落在 `result.json` 的对应字段里，且每条关键结论带 locator + 逐字 quote。

## 1. 任务模型：已知条目检索 + 版本归并

本题三个固定案例是"已知条目（known-item）"类跨语言检索：给定研究问题，题目中通常会给出题名、作者、年份、DOI 或语种等线索，目标是**核实并找回那一个具体作品及其全部版本视图**，而不是返回泛化的相关文献排序列表。因此：

- 第 1 步先区分 `target_work`（要找回的作品）与 `recall_candidates`（检索命中的候选）。
- 每条候选都要回答四个问题：是否同一作品、正文语言是什么、是哪个版本、来自哪个来源。
- 排序依据应写成 `sorting_basis`：优先按"作品一致性 > 版本完整性 > 元数据可信度"排序，而不是按被引量等与任务无关的指标。

## 2. 版本视图分类（务必写依据）

每个作品按"视图（view）"建模，`version` 取值限定为：

| version | 含义 | 判定依据（至少一条） |
|---|---|---|
| `original` | 原始正文（如葡文原文 / 英文原文） | 出版方标注、DOI 解析、正文语言逐字抽样 |
| `full_translation` | 出版方整篇译文 | 出版方"translated by / versão"声明、页脚版权行 |
| `abstract_only` | 仅摘要为某语种，正文为另一语种 | 摘要语种 ≠ 正文语种，且无整篇译文 |
| `duplicate` | 同一作品同一视图的精确重复记录 | 相同 DOI 或 题名+作者+年份 完全一致 |

每个 `result` 项必须带 `work_id`（去重键）与 `view_id`。`work_id` 优先用规范 DOI（小写、去空格）；无 DOI 时用 `lower(title)|lower(author)|year` 的确定性拼接。`dedup` 字段记录每次归并判定及其依据。

## 3. 语言判定（证据优先，脚本只是提示）

- 禁止仅凭 URL、文件名、来源标签或界面语言判断正文语种。
- 可用的直接证据：正文抽样逐字 quote、出版方"语言/translated from"声明、DOI 元数据、摘要语种声明。
- `crosslang.script_hint()` 只区分文字系统（拉丁/汉字/假名/西里尔等），**不能**区分葡语/英语/西语/法语/德语等拉丁系语言；这种情况必须回退到出版方声明或摘要证据，否则语言标 `undetermined` 并写入 `uncertainty`。
- 拉丁系语言可用的辅助线索（仍需人工可复核）：葡语特征词（`artigo`, `como`, `não`, `é`, `sobre`）、英语特征词（`the`, `and`, `of`, `study`）作为**提示**而非结论，`basis` 里写清是 `script_hint` 还是 `publisher_declaration`。

## 4. 术语对齐（双向、可复核）

- 每条术语记录：`source_term`、`source_lang`、`target_term`、`target_lang`、`variants`（同义词/缩写/上下位/音译/本地译名）、`type`、`basis`（来源）、`verification`（如何核对）。
- 双向覆盖：源语种 → 目标语种 与 目标语种 → 源语种 都要有对照，避免单向翻译造成召回偏向。
- 翻译必须记录翻译服务与版本（如 `service:"manual", version:"-"` 或实际使用的服务名与快照版本）；未记录来源的译文标 `unverified_translation`，不得当作已核对。

## 5. 证据溯源（每条关键结论）

- `evidence_map` 每项：`claim`（结论）、`locator`（URL / DOI / 材料段落定位如 `materials/x.pdf#p3`）、`quote`（逐字摘录）、`quote_from`（正文/摘要/元数据/出版方声明）。
- `quote` 必须是材料中的逐字片段；改写内容写 `paraphrase` 并标明，不得冒充 quote。
- 缺失关键数值/元数据 → `not_reported` 或 `pending_human`，不补造。

## 6. 不确定性显式化（本版重点）

`uncertainty` 数组每项带 `field`、`kind`、`basis`、`mitigation`：

| kind | 含义 |
|---|---|
| `language_uncertain` | 语种无法由直接证据确定 |
| `version_uncertain` | 原文/译文/仅摘要无法判定 |
| `translation_unverified` | 译文未复核或翻译服务失败 |
| `metadata_missing` | 年份/DOI/作者等缺失 |
| `source_conflict` | 两个来源对同一指标说法不一致 |
| `coverage_incomplete` | 检索渠道有限，未穷尽 |

`limitations` 数组记录结构性限制（渠道不可用、权限、材料大小上限跳过等），与 `degradations`（异常触发与降级动作）区分：`limitations` 是"本来就没有"，`degradations` 是"本来有但失败了"。

## 7. 降级与预算

- 预算：900 秒、64 次模型请求、每响应含推理 ≤16384 token。前 3 分钟（180 秒）内必须已用 `init` 落盘 partial 骨架，之后只 patch 增量。
- 每步产出即时 patch 回 `result.json`；同一命令失败最多重试 2 次，之后降级并把降级动作写入 `degradations`。
- 网络/检索/翻译工具失败 → `status` 降为 `partial` 或 `insufficient_evidence`，明确已完成与未完成。
- 任何情况下都不编造文献、DOI、数值、语种或版本；无法确定就写 `undetermined` / `not_reported` / `pending_human`。

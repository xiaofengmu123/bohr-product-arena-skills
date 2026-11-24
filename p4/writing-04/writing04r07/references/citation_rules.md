# 引用与文献整理规则（Agent 语义判定用）

本文件规定脚本不做、必须由 Agent 完成的语义判定。脚本只做确定性部分
（解析、去重、编号重排、格式转换、一致性检查），并预留 `evidence_map.json`
与 `change_log` 骨架；下列规则指导 Agent 把语义结论写回，禁止臆造。

## 1. 引用定位（挂得对）

- 引用必须落在需要来源支撑的那句话之后（或句末标点前），不得整段末尾
  或图表注附近随意挂。
- 一句多引用 `[1,2]`；多句一引允许在首句引用后沿用，但需在
  `change_log` 说明沿用到哪些句子。
- 无法判断某句是否需引用时，在 `pending_human` 记录句子定位，不强行插入。

## 2. 论断—文献匹配（对得上）

- 每条引用写入 `evidence_map.json`，字段至少含：
  `reference_id`、`locator`（原稿段落/句子定位，如 `manuscript.md#p3`）、
  `quote`（原稿论断的逐字片段）、`claim`（归纳的论断）、
  `match_rationale`（为什么该文献主题/结论与之匹配）。
- 文献题名/摘要/结论与论断冲突时，标 `source_conflict` 或
  `mismatch`，不强行匹配。
- 元数据只能取自输入材料；材料没有的字段写 `not_reported`，
  不得用外部检索结果补造。

## 3. 去重（收得净）

- 判据优先级：DOI（不区分大小写、剥离 `https://doi.org/` 前缀）>
  规范化题名 + 第一作者 + 年份。
- 合并依据写入 `consistency_report.json` 的 `merge_evidence`，
  说明命中的判据与成员来源文件。
- 同名不同文献（同年不同题名、同题名不同年/不同作者）不得合并；
  无法确定时标 `not_verified` 并列入 `pending_human`。

## 4. 编号与格式（编得顺）

- 顺序编码制：按正文首次出现顺序编号，正文与文末列表同步。
- 著者—出版年制或脚注由 `--style` / 材料约束决定；脚本默认
  `gbt7714`，可传 `apa`。
- 格式转换保留 DOI、卷、期、页、文献类型标识；中英文混排与标点差异
  在 `degradations` 记录，不擅自删字段。

## 5. 降级与诚实

- 缺文献库/缺稿件 → `status = insufficient_evidence`，产物用占位符并
  标明“非真实结果”。
- 坏 JSON/坏编码/解析失败 → 记 `errors`；超限/不支持文件 → 记
  `degradations`，继续处理其余材料。
- DOI 解析失败、条目歧义 → 不自动合并、不声称已核验，标
  `not_verified` / `pending_human` / `source_conflict`。
- 严禁编造评测分数、DOI、元数据、检索结果或实验数据；`status = ok`
  只能在语义核验完成后设置。

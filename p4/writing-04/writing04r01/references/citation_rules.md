# 引用与证据规则（Agent 语义判断依据）

本文是 Agent 在阶段 2–4 做语义判断时的唯一规则来源。脚本只执行确定性步骤；凡涉及“是否匹配、是否冲突、是否可信”的判断，均按本文执行并回写 `evidence_map.json` / `change_log.json`。

## 1. 论断句定位与引用位置

1. 只有承载可证伪事实（数值、结论、主张、归属）的句子才需要引用；纯过渡句、常识句不挂引用。
2. 引用标记必须落在**该论断句句内或句末标点前**，不得整段末尾一挂了之；一句多引用 `[a,b]`，多句一引用在同一句内复用编号。
3. 每条挂载的引用都要回答：这条文献是否真的支撑该句的主题与结论。支撑关系写进该 claim 的 `rationale`。
4. 无法判断支撑关系时，`match_decision = insufficient`，`confidence = not_verified`，并加入 `pending_human`。

## 2. claim ↔ 文献匹配判据

按下列顺序逐条核验，全部通过才写 `matched`：

1. **主题匹配**：claim 的关键词（对象/方法/现象）与文献题名或摘要同义或上下位。
2. **结论方向匹配**：claim 的断言（提升/下降/相关/因果）与文献结论一致，不能仅凭题名相近就判定。
3. **时间可性**：文献发表年不晚于 claim 所描述事实的时间点；未来日期直接标 `source_conflict`。
4. **元数据真实性**：DOI、作者、卷期页、题名来自材料原文，不得外推。

任何一条不满足 → `partial` 或 `conflict`，并在 `notes` 写明缺哪一条。

## 3. 去重与归并判据（可解释）

- **强判据（可自动合并）**：DOI 规范化后相同（去 `https://doi.org/`、去 `doi:`、统一小写）。
- **中判据（需复核后合并）**：规范化题名相同 且（第一作者姓氏相同 且 年份相同）；或其中一侧缺作者/年份且另一侧存在且一致。
- **禁止合并**：作者与年份相同但题名不同（同名作者不同作品）；仅题名相近但作者不同。
- 合并后必须在 `merge_evidence.json` 记录每个字段取自哪个成员（`from_member_indices`），并写 `change_log`。

## 4. 编号重排与交叉引用

- 采用顺序编码制：正文按**首次出现顺序**编号，文末列表同序；`scripts/main.py` 已自动重排 `[n]` 标记。
- 图表注、附录、脚注中的引用同样纳入首次出现顺序；脚本重排后需人工复核这些位置的编号。
- 正文标记与文末列表必须一一对应：`consistency_report.json` 的 `unresolved_citations`（正文有、列表无）与 `uncited_entries`（列表有、正文无）必须逐条处理。

## 5. 格式转换与字段补齐

- 目标规范：`gbt7714`（GB/T 7714—2015，默认）或 `apa`（第 7 版）；脚本 `--style` 切换。
- 关键字段（DOI、卷期页、文献类型标识、出版年）不得丢失；缺失填 `not_reported` 并在 `pending_human` 列出。
- 中英文混排：中文条目保留中文题名/作者，拉丁作者姓氏大写；标点按目标规范处理，不手工臆改原文字段值。

## 6. 来源核验与冲突处理

1. 每条引用必须能在文献库中找到真实条目；找不到 → `unresolved`，不得代填 DOI/元数据。
2. 同一指标/事实出现两个来源且结论不一致 → 两个都记录，标 `source_conflict`，不擅自删减。
3. 文献库内部同一题名但有不同 DOI/年份 → 标 `source_conflict`，交 `pending_human`。
4. 无法核验（无网络/无 DOI 解析/无原文）→ `confidence = not_verified`，写 `pending_human`，**不声称已核验**。

## 7. 证据溯源（locator + 逐字 quote）

1. `locator` 由脚本机械生成（`file` + `line` + `col` + `char_start/char_end`，或 `citekey#field`）；Agent 不得改写 locator。
2. `quote` 必须是 locator 指向位置的**逐字原文**；禁止 paraphrase 冒充 quote。若原文与 locator 不一致，说明文件已被改动，须重新抽取。
3. `evidence.quote` 对文献库字段逐字取值；脚本已预填，Agent 只做核验。
4. 所有回写必须同步 `change_log`：`{action, before, after, locator, time}`。

## 8. 诚实原则

`status=ok` 仅在语义核验完成后设置；材料不足/冲突未解决时如实写 `partial`/`insufficient_evidence`/`input_error`。不得编造 DOI、元数据、检索结果、实验数据或评测分数。

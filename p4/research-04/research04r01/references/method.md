# 方法与增量协议

本文档说明脚本的确定性规则与 Agent 增量协作协议，供复算与二次开发。所有脚本只依赖 Python 标准库，路径相对包根。

## 1. 目录与产物

```text
/input/                  运行时输入：case.json、task.md、materials/（或 case.json 指定路径）
/app/work/               中间产物：materials/mat_XXX.txt、manifest.json、patches/*.json、selfcheck.json
/app/output/             最终产物：result.json、report.md、evidence_map.json、tables/*.csv
```

## 2. 确定性抽取规则（core_impl.py）

- **发现**：递归扫描 `/input/`，忽略隐藏文件；`case.json/task.md/input.json` 等控制文件永不进入语料；单文件 >2MB 跳过、>200 文件截断，均记入 `degradations`。
- **读取**：按 `utf-8-sig → utf-8 → gbk → latin-1` 依次尝试；PDF 先尝试 `pdftotext -layout`，其次环境变量 `PDF_TEXT_TOOL`；均不可用则记 degradation 并置 `parsed=false`，不伪造文本层。
- **年份**：优先级 metadata（year/date/publication_date 等）→ 标题附近 `[2021]` → 正文前 2000 字符内的首个 19xx/20xx；每次记录 `year_source`。
- **被引**：metadata 的 citations/cited_by/times_cited；无则 `null`，不估算。
- **术语候选**：metadata keywords 优先；否则 ASCII 词元（≥4 字符，去停用词）与 CJK 2–6 字串频率前 30，标 `machine=true`。
- **空白/冲突候选句**：按句子拆分后，命中 cue 词（future work / limitation / unknown / however / in contrast / versus 等）的句子，带 `locator` 与逐字句子，标 `machine=true`。
- **年度分布与窗口**：按年份计数；窗口按跨度自动等宽切分（span<3 单窗；3–7 两窗；8–11 三窗；≥12 四窗）；拐点候选为年增量符号翻转且 |Δ|≥2 的点。

所有排序均确定（sorted / 固定字典序）；无随机种子依赖，重复运行结果一致。

## 3. 增量 patch 协议

Agent 深度分析时**不直接改写整份 result.json**，而是写 `/app/work/patches/<类别>.json`，然后重跑 `scripts/main.py`。合并规则（`deep_merge`）：

- 目标与 patch 均为 dict → 递归合并；值为标量/字符串 → patch 覆盖。
- 值为 list 且元素是带 `id` 的 dict → 按 `id` 更新（已存在则递归合并，不存在则追加），因此新增 claims/items 不会覆盖他类。
- 其它 list → patch 覆盖（用于 criteria / sub_questions 等整体替换）。

约定 patch 只含要改的字段，文件名按类别排序后确定性合并：`01_time_trend.json`、`02_topic_evolution.json`、`03_representative_works.json`、`04_research_gaps.json`、`05_evidence_map.json`、`06_finalize.json`。每次合并后脚本自动 `rebuild_evidence_index`（补齐缺失 `quote_hash`，重建 by_claim/by_doc/by_status）。

## 4. 四类产物的证据回链

- claim 统一 `{id,claim,support,evidence_ids[],confidence,rationale?}`；`evidence_ids` 必须指向 evidence_map.items 的 `id`。
- evidence 条目 `used_by_claims` 反向回指，形成双向索引；selfcheck 会报出悬空引用与“quote 未定位”。
- 同一 claim 有多条证据时，evidence_map 逐条独立记录（每条一行定位），不把多个来源折叠成一条。
- 冲突：两方证据各占一条 evidence（`status=source_conflict`），并同时出现在 conflicts.items 的 `sides`；分辨率写在 `resolution`，无法裁决写“未决”。

## 5. 空白排除证据的写法

每条 gap 的 `exclusion_evidence` 至少包含四要素：**检索式/检索范围**、**纳排条件**、**反例排查方式**、**结论边界**。示例（示意，非正式答案）：

> 检索式 `("term A" AND "term B")`，范围 = 2020–2025 年题录 + 摘要；排除纯应用类与无同行评议预印本；反例排查在 `materials/` 全文中搜索 “term C” 0 命中；结论仅限给定语料，不宣称全领域无此类工作。

## 6. 状态与可复算性

- `status` 判定见 `references/checklist.md` 第 6 节；由收尾 patch 写入，脚本在无 patch 时自动按 completeness 推断。
- 可复算表 `tables/*.csv` 由 result.json 确定性导出，供外部复算年度分布、术语演化与证据行定位。
- `evidence.csv` 含每条的 id/doc_id/claim/locator/status/confidence/kind/quote，可直接按 locator 打开原文核对。

## 7. 边界

- 本 Skill 解决“给定文献集内的前沿/趋势/空白综述”，不替代跨库检索；语料边界如实写入 limitations。
- 不做预置答案：脚本只产出候选与统计，结论必须由 Agent 读原文核验后经 patch 写入。
- 不伪造软件状态、数据、文献或分数；任何失败均以 status/errors/degradations/limitations 显式记录。

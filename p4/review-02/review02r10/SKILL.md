---
name: citation-fact-audit
description: >
  Audit a scientific paper or research material for citation support, factual
  reliability and evidence traceability. The workflow is event-sourced: every
  finding, verdict and metadata change is appended as one JSON line to an
  append-only journal, and a deterministic compiler replays the journal into
  result.json + report.md. Each finding carries a locator, verbatim quote,
  verdict, severity and revision suggestion across seven review categories,
  and a machine verifier flags non-verbatim quotes before human review.
  Use when the task asks for citation and fact verification or paper quality
  review with reproducible, itemized findings.
version: 2.0.0
---

# 引用与事实核验审查 Skill（事件日志版）

## 1. 角色与何时使用

你是一名科研论文质量审查员，只做"引用与事实核验"：按检查项逐条审查输入，对每个
问题给出原文定位、逐字摘录、判定、严重度与修改建议。适用条件：

- 任务要求做论文/科研材料的引用、事实、方法、统计、逻辑或规范审查；
- 需要交付可被他人按记录复核的**条目化结论清单**（而非一段总体评价）。

不做：不改写论文、不做润色、不做数据分析建模、不替代作者对结论的判断。文献阅读
与数据分析只提供材料，不替代审查结论。本 Skill 只计四件事：问题发现、证据定位、
严重度判断、修改建议。判定基准见 `references/checklist.md`；result.json 完整契约
见 `references/result_schema.md`。执行前先完整阅读这两个文件。

## 2. 输入契约

运行时输入固定为：

- `/input/case.json`：案例标识（`case_id`）、任务说明与 `output_contract`（若有）。
- `/input/task.md`：任务与检查项说明（中文）。
- `/input/` 下的材料：实际论文/科研材料，常见为 PDF；也可能是 txt/md/csv/json。

工作目录 `/app/work`，最终产物目录 `/app/output`。以实际文件为准，不要依据目录名
或领域标签假设材料内容。PDF 解析用真实 PDF，无预抽答案层。

工具脚本（相对路径，均在 Skill 包根目录运行，即 `/skills/weekly-skill`）：

- `scripts/main.py`：唯一入口；子命令见 §5。
- `scripts/core_impl.py`：实现（事件日志 + 确定性编译 + 逐字校验）。
- `scripts/selftest.py`：机器自检（`python -B scripts/selftest.py`）。

三者只用 Python 标准库；`--input/--work/--output` 缺省为 `/input`、`/app/work`、
`/app/output`，且可出现在子命令之后。先 `cd /skills/weekly-skill`（或用绝对路径）
再执行。

## 3. 输出契约

必须产出 `/app/output/result.json`（机器可复核）与 `/app/output/report.md`
（人类可读、可编辑）。result.json 顶层字段：

- `case_id`；`status`：`ok|partial|insufficient_evidence|input_error`。
- `summary.verdict`：`pass|issues_found|needs_review|unchecked|insufficient`；
  `summary.counts`：total/critical/major/minor/suggestion 及 by_category、
  by_finding_type；`summary.coverage`：已查/未查类别与方法/结果/讨论等章节。
- `review_report.findings`：逐条问题，每条含 id/category/locator/quote/verdict/
  severity/suggestion（必填）及 finding_type/claim/basis/source_ref/confidence/
  review_status/quote_verified。
- `evidence.evidence_map`：每条 finding 的 `finding_id+locator+quote+basis+
  source_ref+quote_verified`。
- `output_contract`：若 case.json 含之，全部字段**原样回填**（不改名、不删未知键、
  不填值），并并入顶层（标准字段优先）。
- `sources`、`uncertainty`、`pending_human`、`limitations`、`errors`、
  `degradations`、`change_log`、`runtime`。

`quote` 必须逐字摘自原文；`locator` 用可回查定位串。`status` 要诚实：材料不足写
`insufficient_evidence`，绝不编造数据或结论。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

原则：**前 3 分钟内先落盘一版 result.json 骨架，之后只做增量改进**；每阶段结束
立即 `compile` 刷新结果，任何中断都留下合法产物。控制请求数，避免重复读同一段
大文本。

**阶段 0（0–180 秒，≤8 次请求）——发现与骨架**
1. `python -B scripts/main.py discover`；读 stdout 与 `/app/work/manifest.json`。
2. `python -B scripts/main.py init`；落盘骨架 result.json（status=`partial`、
   verdict=`unchecked`、findings=[]）与 `journal.jsonl` 首个事件。
3. 读 `/input/case.json` 与 `/input/task.md`，确认 case_id、检查项、材料清单与
   降级点，写入工作记忆。
4. 若 manifest 报错（缺 case.json/材料不可读/缺 pdftotext），立即用
   `meta --key degradations` 记录并 `compile`，进入降级。

**阶段 1（180–480 秒，≤20 次请求）——逐段审查与逐条落盘**
1. 按材料逐段读原文：PDF 读 `/app/work/texts/*.txt`（页标记）或
   `/app/work/pages/*/p###.txt`；文本材料读对应 txt。一次读 2–3 页/段，读完即审，
   不回读。
2. 对照七类检查项逐条找问题；每发现一条，立即写 `/app/work/finding.json` 并用
   `python -B scripts/main.py add --file /app/work/finding.json` 追加事件（或直接
   向 `journal.jsonl` 追加一行合法 JSON，二者等价）。
3. 引用支持按四档判定：`fully_supported/partially_supported/unsupported/
   undeterminable`；区分"引用了"与"支持了"，识别 overreach。
4. 每完成一类或一页，`python -B scripts/main.py compile` 增量刷新（输出始终反映
   当前进度）。

**阶段 2（480–780 秒，≤25 次请求）——复核与交叉核对**
1. 对每条已落盘 finding 复核：重读对应原文片段，确认 quote 逐字、locator 准确、
   verdict 与引文语义一致、severity 匹配；`python -B scripts/main.py verify` 看
   `quote_not_verbatim`/缺键告警并修正。
2. 交叉核对摘要/正文/表格/图注的数值、样本量、显著性表述，矛盾处标
   `inconsistency` 且两条证据都记录。
3. 无法确认的条目：`set --key status`/`set --key verdict` 为诚实值，
   `review_status` 改 `needs_review`，用 `meta --key uncertainty/pending_human`
   记录；未取得来源写 `source_ref=not_verified`。
4. 用 `meta` 补齐 `sources/limitations/errors/degradations`，`compile`。

**阶段 3（780–900 秒，≤11 次请求）——收尾与自检**
1. 跑 §6 自检清单；修正缺键、缺定位串、非逐字 quote。
2. 最终 `python -B scripts/main.py compile` 与 `verify`；确认 status/verdict 正确、
   result.json 可 `json.load`、report.md 已生成。
3. 时间不足则保留 partial 产物并在 `limitations` 写明未完成项，绝不补造结论。
时间不够时优先保证：骨架已落盘 → 关键章节高危问题已落盘 → 每条有定位与摘录 →
状态诚实。宁缺毋滥。

## 5. 脚本调用速查

```bash
python -B scripts/main.py --help                       # 退出码 0
python -B scripts/main.py discover                     # 扫描+抽文本 -> work/manifest.json
python -B scripts/main.py init                         # 落盘 partial 骨架（幂等）
python -B scripts/main.py add --file work/finding.json # 追加一条 finding 事件
python -B scripts/main.py set --key status --value ok
python -B scripts/main.py set --key verdict --value issues_found
python -B scripts/main.py meta --key sources --items '[{"ref":"…","status":"verified"}]'
python -B scripts/main.py compile                      # 日志确定性重放 -> result.json + report.md
python -B scripts/main.py verify                       # 机器校验必填键 + quote 逐字
python -B scripts/main.py run                          # discover+init+compile 一键骨架
python -B scripts/selftest.py                          # 全量机器自检（临时夹具）
```

`add` 的 finding JSON 最少含 `id` 与七必填键（`id/category/locator/quote/verdict/
severity/suggestion`）；`compile` 会对缺键/非法枚举/非逐字 quote 写 `errors`。

## 6. 自检清单（逐条核对，对应评分维度）

- [ ] result.json 存在且可 `json.load`；含 case_id 与 output_contract 全部字段。
- [ ] 每条 finding 七必填键齐全：id/category/locator/quote/verdict/severity/suggestion。
- [ ] 每条结论能按 `locator + quote` 回查原文，`quote` 逐字（`quote_verified=true`）。
- [ ] 判定与引文语义一致，"引用存在"与"引用支持结论"已区分。
- [ ] 七类检查项与方法/结果/讨论等关键章节均有覆盖或明确标注未覆盖。
- [ ] 冲突信息未隐藏：同一指标不一致时两条证据都在，标 `inconsistency`。
- [ ] 无法确认的条目已标 `needs_review` 并进 `uncertainty`/`pending_human`。
- [ ] sources 只含实际核验过的来源；未取得写 `not_verified`，无虚构 DOI/URL。
- [ ] status 与 verdict 诚实；空材料为 `insufficient_evidence`。
- [ ] `selftest.py` 全绿；同一 journal 重复 `compile` 结果一致（无随机数）。

## 7. 降级策略

遇到以下情况**不崩溃、不编造**，如实记录并继续处理其余材料：

| 情况 | 处置 |
|---|---|
| 空输入目录/无材料 | status=`insufficient_evidence`，verdict=`insufficient`，limitations 列出缺口 |
| 坏 JSON / 坏编码 | 记入 errors/degradations，跳过该文件，继续读其它材料 |
| 缺 pdftotext | manifest 记 degradation，该 PDF 标 `needs_ocr`，提示改用 OCR 工具 |
| 扫描版 PDF（抽出空文本） | 标 `needs_ocr`，改走 OCR；无法识别则不判结论 |
| 只有摘要/原文取不到 | 相关结论标 `undeterminable`/`needs_review`，不硬判 |
| 引文严重歧义/来源版本不一致 | 两条都记录并标 `source_conflict`，进 uncertainty |
| 写盘失败 | 尝试降级到最小合法产物；仍失败则把错误写 stderr 并退出非 0 |
| 数据库/权限不足 | 记 limitations，该来源写 `not_verified` |

任何"缺失"都写 `not_reported`/`not_verified`，禁止虚构引用、DOI、数值、来源或
撤稿状态；禁止编造评测分数与运行结果。

## 8. 本版架构差异（相对参考包）

参考包用"一 finding 一文件 + finalize 合并"；本版改为**单条 append-only 事件日志 +
确定性编译器 + 机器逐字校验**，取舍如下：

1. 事实源唯一：所有变更只追加到 `journal.jsonl`，历史永不丢失，可审计、可重放；
   不再有分散的 finding 文件与合并顺序问题。
2. 产物是日志的纯函数：`compile` 无随机数、findings 按 id 排序、meta 按事件顺序
   去重合并，同一日志产出逐字节一致。
3. 校验顺序前移：`verify` 在人工复核前自动标 `quote_not_verified`（非逐字）与
   缺键，把"证据定位"变成机器可证伪的硬约束，而非事后声明。
4. 契约回填保证：`output_contract` 全部键原样回填并并入顶层，标准字段优先，
   未知键不丢。

## 9. 最小调用示例（示意，非正式案例）

```bash
mkdir -p demo/input && echo "The effect was 12.4 (n=40)." > demo/input/materials.txt
echo '{"case_id":"demo","output_contract":{"deliverable":"result.json"}}' > demo/input/case.json
python -B scripts/main.py run --input demo/input --work demo/work --output demo/output
python -B scripts/main.py verify --work demo/work --output demo/output
```

示意输出：`compiled: status=partial verdict=unchecked findings=0`，随后按 §4 追加
findings 并 `compile`。示意输出仅说明接口与流程，不冒充正式案例结果。

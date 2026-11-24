---
name: evidence-bound-stats-audit
description: >
  Audit a fixed paper or research material for research design, variable
  definition, statistical methods, sample size/power, and results reporting,
  and deliver an itemized, source-bound review: every finding carries a
  locator, a verbatim quote mechanically verified against the extracted text
  (SHA-1 + char span), a defect type, severity, judgment basis and a revision
  suggestion. Use when the task asks for research-methods and statistical
  review of a manuscript, and requires reproducible, evidence-bound findings.
version: 1.0.0
---

# 研究方法与统计审查 Skill（证据溯源强化版）

## 1. 角色与何时使用

你是一名研究方法与统计审查员，只做一件事：把一篇稿件审查成能交给作者逐条回复的
**审查单**——问题在哪一段/哪张表/哪一句、哪类缺陷、严重程度、依据、怎么改。适用：

- 任务要求对固定论文/科研材料做研究设计、变量定义、统计方法、样本量、结果报告审查；
- 需要交付条目化、可复核、可独立重跑的结论清单，而非一段读后感。

不做：文献检索与综述、全文翻译、语言润色、代写审稿结论、投稿决策、数据再建模。
本 Skill 只负责：问题发现、证据定位、严重度判断、修改建议。

判定基准与方法—数据类型匹配表见 `references/checklist.md`；result.json 契约与
证据溯源结构见 `references/result_schema.md`。**执行前先完整读完这两个文件。**

## 2. 输入契约

运行时输入固定为：

- `/input/case.json`：案例标识（`case_id`）、任务说明、`output_contract`（若有）。
- `/input/task.md`：任务与检查项说明。
- `/input/` 下材料（含 `materials/` 子目录）：实际论文/科研材料，常见为 PDF，也可为
  txt/md/csv/json/docx。PDF 用真实 PDF 解析（`pdftotext`），无预抽答案层。

工作目录 `/app/work`，最终产物目录 `/app/output`。**以实际文件内容为准**，不要依据
目录名或领域标签假设材料内容。

脚本（相对路径，在 Skill 包根目录 `/skills/weekly-skill` 下运行）：

- `scripts/main.py discover`：发现输入、抽取文本（PDF 页级定位）、建定位索引、写 manifest/segments/terms。
- `scripts/main.py scaffold`：**前 3 分钟**先落盘合法 result.json 骨架与 meta.json/state.json。
- `scripts/main.py finalize`：合并 findings、逐字 quote 校验、数值一致性候选检查、校验并渲染 report.md。
- `scripts/selftest.py`：在临时目录生成合成夹具，跑通全流程并断言契约不变量。

三者只依赖 Python 标准库；`--input/--output/--work` 缺省为 `/input`、`/app/output`、
`/app/work`。执行如 `python -B scripts/main.py discover`；不在包根则用绝对路径
`/skills/weekly-skill/scripts/main.py`。每次调用结束用 `--help` 确认退出码 0。

## 3. 输出契约

必须产出 `/app/output/result.json`（机器可复核）与 `/app/output/report.md`（人类
可读、可编辑）。result.json 顶层字段：`case_id`、`status`、`output_contract`、
`summary`、`result_status`、`review_report.findings`、`evidence`、`sources`、
`uncertainty`、`pending_human`、`limitations`、`errors`、`degradations`、
`change_log`、`runtime`，语义见 `references/result_schema.md`。

- 若 `case.json` 含 `output_contract`，`scaffold` **原样回填**其全部字段（缺值给
  `null`/`[]`/`""`，不改名不删未知键），并把非保留键提升为 result.json 顶层；
  `finalize` 合并时保留所有未知顶层字段。
- 每条 finding 必填 `id/facet/locator/quote/severity/suggestion`，另有
  `finding_type/check_class/rationale/confidence/review_status/evidence`。
- **证据溯源（本版强化点）**：每条 finding 的 `quote` 必须逐字摘自原文；
  `finalize` 会把 quote 与 `/app/work/segments.json` 逐字比对，回填
  `evidence.source/segment_id/quote_sha1/char_offset/context/verification`，并写入
  `evidence.evidence_map`。`verification=none` 时自动降级为 `needs_review` 并进
  `uncertainty`/`degradations`，绝不冒充已验证证据。
- `quote` 不得改写、不得拼接非相邻文字；`locator` 用可回查串（`p.3 ¶2` /
  `p.4 Table 1` / 纯文本 `¶2`）。`status` 要诚实：材料不足写 `insufficient_evidence`，
  绝不编造数据、数值、引用或结论。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

原则：**前 3 分钟内先落盘一版 result.json 骨架，之后只做增量改进**；每阶段结束立即
刷新结果，任何中断都留下合法产物。控制请求次数，不回读已读文本。

**阶段 0（0–180 秒，≤8 次请求）——发现与骨架**
1. `python -B scripts/main.py discover`，读 stdout 与 `/app/work/manifest.json`、
   `/app/work/terms.json`、`/app/work/segments.json`。
2. `python -B scripts/main.py scaffold`，落盘 `/app/output/result.json` 骨架
   （status=`partial`、verdict=`unchecked`、findings=[]）与 `meta.json`/`state.json`。
3. 读 `/input/case.json` 与 `/input/task.md`，确认 case_id、检查项、材料清单。
4. manifest 报错（缺 case.json/材料不可读/缺 pdftotext）→ 写 `/app/work/meta.json`
   的 `errors/degradations` 并重跑 finalize，进入降级。

**阶段 1（180–480 秒，≤20 次请求）——按检查面逐段审查、逐条落盘**
1. 用 `/app/work/terms.json` 定位方法/结果段落；一次读 2–3 页/段，读完即审，不回读。
2. 对照 `references/checklist.md` 五个检查面逐条找问题；每发现一条，立即写成
   `/app/work/findings/<id>.json`（一个文件一个对象，字段见 result_schema §3）。
3. 严格区分 `factual_error / insufficient_evidence / normative_issue / advisory /
   pending_review`，严重度匹配影响面（拿不准一律降一档并注明“影响面待确认”）。
4. 每完成一个检查面，运行一次 `python -B scripts/main.py finalize` 增量刷新
   result.json 与 report.md。

**阶段 2（480–780 秒，≤25 次请求）——复核与交叉核对**
1. 逐条回读 finding 对应原文，确认 quote 逐字、locator 准确、严重度与性质匹配。
2. 看 finalize 输出的 `evidence.numeric_checks` 候选（p 值与措辞/CI、多处 N/n 不一致），
   确证者提升为 finding，不确证者不写或写 `pending_review`。
3. 交叉核对摘要/正文/表格/图注的数值、样本量、显著性表述；矛盾处两条证据都记录。
4. 无法确认的条目：`review_status=needs_review`，写入 `uncertainty`/`pending_human`。
5. 填 `/app/work/meta.json`：`status/overall_verdict/overall_assessment/
   checked_facets/sources/uncertainty/limitations/...`，重跑 finalize。

**阶段 3（780–900 秒，≤11 次请求）——收尾与自检**
1. 跑 §5 自检清单；修正缺必填键、缺定位串、非逐字 quote。
2. 最终 `python -B scripts/main.py finalize`；确认 result.json 可 `json.load`、
   report.md 已生成、status/verdict 诚实。
3. 时间不足时保留当前 partial 产物，在 `limitations` 写明未完成检查项，绝不补造。

优先级：骨架已落盘 → 关键章节高危问题已落盘 → 每条有定位+摘录 → 状态诚实。宁缺毋滥。

## 5. 自检清单（逐条核对，对应评分维度）

- [ ] result.json 存在且 `json.load` 可解析；含 case_id 与 output_contract 全部字段。
- [ ] 每条 finding 六必填齐全：id/facet/locator/quote/severity/suggestion；finding_type/
  check_class/confidence/review_status 显式填写。
- [ ] 每条结论能按 `locator + quote` 回查原文；`evidence.verification=exact`（未命中项
      已自动降级 needs_review，不得伪装 confirmed）。
- [ ] 五个检查面（研究设计/变量定义/统计方法/样本量/结果报告）覆盖或显式标注未覆盖。
- [ ] 事实错误、证据不足、规范问题、建议性意见、待复核五类已区分。
- [ ] 严重度与缺陷性质匹配：未把“没报告”写成硬伤，也未把硬伤写成建议。
- [ ] 方法判定区分“方法不当/可接受但报告不全/可接受可改”三档。
- [ ] `numeric_checks` 候选已逐条复核，确证者入 findings，其余不冒充结论。
- [ ] 冲突信息未隐藏；无法确认条目标 `needs_review` 并进 uncertainty/pending_human。
- [ ] sources 只含实际核验来源；无虚构 DOI/URL/数值；status 诚实。
- [ ] 脚本标准库、`--help` 退出码 0、`python -B scripts/selftest.py` 通过、同一输入
      重复运行结果一致（除 runtime/change_log 时间戳外无随机数）。

## 6. 降级策略

遇以下情况**不崩溃、不编造**，如实记录并继续处理其余材料：

| 情况 | 处置 |
|---|---|
| 空输入目录/无材料 | status=`insufficient_evidence`，limitations 列出缺口 |
| 缺 case.json 且无材料 | status=`input_error`，errors 记录 |
| 坏 JSON/坏编码 | 记入 errors/degradations，跳过该文件继续读其它材料 |
| 缺 pdftotext | degradation 记录，该 PDF 标 `needs_ocr` |
| 扫描版 PDF（空文本） | 标 `needs_ocr`；无法识别则不判结论 |
| 仅摘要/原文取不到 | 相关结论标 `pending_review`，不硬判 |
| 来源版本不一致/严重歧义 | 两条都记录并标 `source_conflict`，进 uncertainty |
| 写盘失败 | 尝试降级到最小合法产物；仍失败写 stderr 并退出非 0 |
| 图表无法解析 | limitations 记录，正文可用则审正文 |
| quote 未逐字命中 | finalize 自动降级 needs_review 并进 uncertainty，不冒充 confirmed |

任何“缺失”都写 `not_reported`/`not_verified`，禁止虚构引用、DOI、数值、来源、撤回
状态、软件状态与评测分数。

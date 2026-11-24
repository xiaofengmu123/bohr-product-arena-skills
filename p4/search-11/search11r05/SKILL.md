---
name: sci-patent-search
description: 专利与技术文献检索 Skill：界定检索范围，规范化公开号/申请号/授权号/优先权号，按共用优先权归并同族并标注口径，核验法律状态，检索同主题技术文献，逐条保留来源与相关性依据，并把 result.json 字段逐一对齐 case.json 的 output_contract（含 change_log 复核轨迹），交付可复核的 /app/output/result.json。当需要专利查新、号码定位、同族归并、法律状态核验或技术情报检索时使用。
version: 1.1.0
---

# 专利与技术文献检索 Skill

## 1. 角色与何时使用

把"专利/技术文献检索"沉淀为可重复执行、可被他人复核的流程。核心不是查多少库，而是三件事：**结果只来自有授权的来源并标注库名、版本与查询日期；公开号/申请号/授权号/同族/法律状态对得上官方登记口径；检索与核验过程可被他人按 change_log 复跑。**

一次真实检索须同时回答五件事并逐项落字段给证据：(1) 专利保护什么（权利要求逐字引用，不越界概括）；(2) 与同族其他成员的关系（简单族/扩展族/观测族口径分开）；(3) 现在是否有效（状态+生效日+来源+查询日期+滞后风险）；(4) 同主题还有谁在做（区分专利文献与技术文献的检索逻辑）；(5) 这些信息是否有权访问与展示（绿/黄/红依赖分级与降级）。

适用：号码定位与规范化、主题检索式构造、同族归并、法律状态核验、技术文献发现、授权与保密分级。不适用：专利撰写、侵权判定、自由实施分析、价值评估（不替代律师/法律意见）；不绕过付费墙、不登录受限库、不上传或展示受限库原文。

## 2. 输入契约（运行时）

- 原始输入在 `/input/`：`case.json`（含 `case_id`、任务说明、可能的 `output_contract`）与 `task.md`，另可能有 `materials/`、`notes.md`、PDF 等。
- **扫描而非假设**：不假设固定文件名/键名，用 `python3 scripts/main.py scaffold` 发现输入。PDF 由平台解析工具提供文本层，扫描件 OCR 可能粘连；**读实际文本，不依据标签脑补内容**。
- 文本按 `utf-8-sig` 容错读取（utf-8→gb18030→latin-1 逐级降级）；单文件 2MB 上限，超限跳过并记录；坏 JSON/坏编码降级不崩溃。
- 工作目录 `/app/work`；最终产物与可编辑工件写到 `/app/output/`。
- 案例含有限专利记录集、USPTO 扫描件、号码类与主题类查询、WO OCR 边界合并。号码类先定位再核验；主题类用分类号/申请人/引证组合检索。

## 3. 输出契约（本版重点：字段逐一对齐 output_contract + change_log）

必须产出 `/app/output/result.json`。**分两层**：

**(a) 契约层**：若 `case.json` 提供 `output_contract`（或 `outputContract`），其全部字段**递归镜像**进 result.json，缺值给 `null`/`[]`/`{}`，不改名、不删未知键；`scaffold` 自动完成并在 `contract_coverage` 中逐字段记录 `{path, type, filled, value_source}`。若契约与受管核心字段（`case_id/status/change_log/runtime/sources/uncertainty` 等）重名，以 Skill 实际值为准，对齐关系记录在 coverage 中。

**(b) 受管层**（无契约时即为默认 schema，Agent 在此基础上增量填充）：
- `case_id`（非空，缺则 `unknown` 并写 uncertainty）与 `status`：`ok | partial | insufficient_evidence | input_error`。
- `research_question`、`retrieval_scope`；`patent_records[]`（每件含 `record_id`、公开号/申请号/优先权号 raw+normalized、`number_type`、权利要求逐字引用、`locator`+`quote`、`confidence`）；`families[]`（成员、`family_type`、`exhaustive:false`、依据与证据）；`legal_status[]`（`status/effective_date/source/as_of/staleness_risk/confidence`）；`literature[]`（`rank/title/authors/year/venue/doi/source/relevance_rationale/evidence`）；`authorization[]`（`source/license_basis/tier/displayable_fields/retention`）；`evidence[]`（每结论 `locator`+逐字 `quote`，冲突标 `source_conflict`）；`sources[]`（`engine/query/url/retrieved_at/count/note`）。
- `contract_coverage{}`、`uncertainty[]`、`pending_human[]`、`errors[]`、`degradations[]`、`limitations[]`、`change_log[]`、`runtime{}`、`input_inventory[]`。

**change_log 纪律**：任何结构变更只经 `record_change` 写入，自动生成 `{seq, at, action, detail, status_after}`；禁止旁路改字段。日志须可被他人按序重放。`finalize` 会校验 seq 连续与 at 单调。

除 `result.json` 外可附 `report.md`（finalize 生成）。status 要诚实：材料不足标 `insufficient_evidence`，运行失败标 `input_error`，不编造文献、号码、状态或分数。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

时间预算为上限；**前 3 分钟内必须先落盘一版合法 partial result.json**，之后只做增量改进。每步若已有合法产物就跳过重复写盘。

**Step 0 — 读输入并落盘骨架（0–60s，≤4 次请求）**：执行 `python3 scripts/main.py scaffold`（默认 `--input /input --output /app/output --work /app/work`）。产出 `/app/output/result.json`（status=partial）+ `/app/work/state.json` + 输入清单 + 契约镜像。scaffold 失败按 §6 手工写最小 JSON。

**Step 1 — 明确问题与检索范围（60–180s，≤6 次请求）**：读 case.json/task.md，判定"号码定位"或"主题检索"。回填 `research_question/retrieval_scope`；识别数据源可用性，写 `authorization` 绿/黄/红。每步用 `record_change` 记日志。

**Step 2 — 号码规范化与同族归并（180–540s，≤22 次请求）**：逐号跑 `python3 scripts/main.py normalize --text "<号码>"`；对扫描件/OCR 文本跑 `python3 scripts/main.py extract --file <文本>` 找候选再逐条核对。records 写 JSON 后跑 `python3 scripts/main.py family --file records.json` 归并，**标 `observed` 且 `exhaustive:false`**。核对权利要求逐字引用与法律状态来源。

**Step 3 — 主题检索与证据对齐（540–780s，≤24 次请求）**：主题检索优先 paper_search/web_search（官方登记页、出版商、预印本），逐条写 `sources` 与 `evidence`；号码核验优先官方库（USPTO/Espacenet/PATENTSCOPE/国家局）。冲突并列，不静默择一。

**Step 4 — 去重、补缺口、收尾（780–900s，≤8 次请求）**：执行 `python3 scripts/main.py finalize`（校验契约、刷新 `contract_coverage`、补 runtime、生成 report.md）；补 `uncertainty/pending_human/errors/degradations/limitations`；确认 status 诚实。空结果写 `insufficient_evidence`，不重开大检索；逼近超时立即落盘并标 partial。

**预算护栏**：单响应 ≤16384 tokens（含推理）；每条结论必须有 locator+quote；检索失败用状态字段说明，不补造文献。

## 5. 自检清单（逐条可勾选，对应评分维度）

见 `references/selfcheck-checklist.md`（含契约完整性、正确性、授权边界、预算降级、可复用五组）。收尾前必跑：

```bash
python3 scripts/main.py --help        # 退出码 0
python3 scripts/main.py scaffold      # 早落盘 partial
python3 scripts/main.py mirror --file /input/case.json   # 检查契约镜像
python3 scripts/main.py finalize      # 校验 + 补 runtime + 刷新 coverage + report.md
python3 scripts/main.py validate      # 必须打印 VALID
python3 scripts/selftest.py           # 空目录/坏JSON/缺输入/正常契约 四场景全 PASS
```

## 6. 降级策略（不许编造）

- 空输入/无材料 → `insufficient_evidence`，`pending_human` 列缺口，退出码 0。
- 坏 JSON/坏编码 → 记 `errors/degradations`，继续处理其余材料。
- 检索无命中/限流/鉴权失败 → 如实写 `sources` 与 `limitations`，标 `partial`，不补造文献。
- 法律状态冲突/过期 → 双方证据并列标 `source_conflict`，写 `staleness_risk` 与 `pending_human`；官方状态未知写 `unknown`，不推断"有效"。
- 字段缺失/OCR 边界不明 → `uncertainty`（`field_missing/ocr_uncertain/family_scope_unclear`），写 `not_reported`，不硬猜。
- 受限库/付费墙 → 标 `degradations` 说明不可用与降级方案，不登录、不绕过。
- 缺软件/接口不可用 → 标 `input_error` 或 `partial`，说明缺失项，不伪造运行记录或分数。
- 写盘失败 → 降级为最小合法 result.json，进程不崩溃。

## 7. 脚本与最小调用示例（示例，非正式案例答案）

均在 Skill 根目录用相对路径调用，纯标准库，可用 `--help` 查看：

- `scripts/main.py`：`scaffold / normalize / extract / family / mirror / finalize / validate` 七子命令。
- `scripts/core_impl.py`：容错读取、契约镜像、号码规范化/抽取、同族归并、校验与 report 生成的工具库。
- `scripts/selftest.py`：提交前四场景自检。

```bash
python3 scripts/main.py scaffold --input /input --output /app/output --work /app/work
python3 scripts/main.py normalize --text "US 12,345,678 B2"
python3 scripts/main.py family --file /app/work/records.json
python3 scripts/main.py finalize --status ok
```

示意 `result.json`（仅接口示例，不冒充正式案例结果）：`{"case_id":"case-demo","status":"ok","contract_coverage":{...},"patent_records":[...],"families":[...],"legal_status":[...],"literature":[...],"evidence":[...],"sources":[...],"uncertainty":[...],"change_log":[...],"runtime":{...}}`。

字段口径、同族归并规则、法律状态核验、授权边界详见 `references/normalization-and-family.md`。

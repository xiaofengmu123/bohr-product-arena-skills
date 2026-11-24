---
name: sci-patent-search
description: 专利与技术文献检索 Skill：把 900 秒 / 64 次请求拆成阶段预算并在前 3 分钟先落盘 partial 骨架，随后增量改进；规范化公开号/申请号/优先权号，按共用优先权归并观测同族并标注口径，核验法律状态与权利范围，检索同主题技术文献，逐条保留 locator+quote 证据，交付可复核的 /app/output/result.json。当需要专利查新、号码定位、同族归并、法律状态核验或技术情报检索时使用。
version: 1.1.0
---

# 专利与技术文献检索 Skill

## 1. 角色与何时使用

本 Skill 把"专利/技术文献检索"沉淀为可复跑、可复核的流程。核心不是查多少库，而是：**结果只来自有授权的来源并标注库名/版本/查询日期；号码、同族、法律状态对得上官方登记口径；检索与核验过程可被他人按记录复跑。**

适用：专利号/族定位与规范化、技术主题检索式构造、法律状态核验、同主题技术文献发现、授权与保密边界分级。不适用：专利撰写、侵权判定、自由实施分析、专利价值评估（不替代律师/法律意见）；不绕过付费墙、不登录受限库、不上传或展示受限库原文。

一次真实检索须同时回答五件事，逐项落字段并给证据：①保护什么（权利要求逐字引用，不越界概括）；②与同族成员什么关系（简单/扩展/观测族口径分开）；③现在是否有效（状态+生效日+来源+查询日期+滞后风险）；④同一主题还有谁在做（区分专利文献与技术文献检索逻辑）；⑤是否有权访问与展示（绿/黄/红分级与降级方案）。

## 2. 输入契约（运行时）

- 原始输入在 `/input/`：`case.json`（含 `case_id`、任务说明、可能的 `output_contract`）与 `task.md`，另可能有 `materials/`、`notes.md`、PDF/扫描件等。
- **扫描而非假设**：不假设固定文件名/键名；用 `python3 scripts/main.py scaffold` 发现输入。PDF 由平台解析工具提供文本层，扫描件 OCR 可能粘连；**读实际文本，不依据标签脑补内容**。
- 文本按 `utf-8-sig` 容错读取；单文件 2MB 上限，超限跳过并记录；坏 JSON/坏编码降级不崩溃。
- 工作目录 `/app/work`；最终产物与可编辑工件写到 `/app/output/`。
- 案例可能包含：有限专利记录集、USPTO 扫描件、号码类与主题类查询、WO OCR 边界合并。号码类查询先定位再核验；主题类查询用分类号/申请人/引证组合检索。

## 3. 输出契约

必须产出 `/app/output/result.json`。若 `case.json` 提供 `output_contract`，其全部字段**原样回填**（缺值给 `null`/`[]`，不改名、不删未知键）；无契约时用默认 schema 增量填充：

- `case_id`（非空，缺则 `unknown` 并写 `uncertainty`）与 `status`：`ok | partial | insufficient_evidence | input_error`。
- `research_question`、`retrieval_scope`：研究问题与检索范围界定。
- `patent_records[]`：每件目标专利，含 `record_id`、公开/申请/优先权号（raw + normalized）、`number_type`、权利要求逐字引用、来源 `locator`+`quote`、`confidence`。
- `families[]`：同族归并，含成员、`family_type`（simple/extended/observed）、`exhaustive:false`、归并依据与证据。
- `legal_status[]`：`status`、`effective_date`、`source`、`as_of`、`staleness_risk`、`confidence`。
- `literature[]`：同主题技术文献，含 `rank/title/authors/year/venue/doi/source/relevance_rationale/evidence`。
- `authorization[]`：数据源许可分级 `{source, license_basis, tier, displayable_fields, retention}`。
- `evidence[]`：每条结论的 `locator` + 逐字 `quote`；冲突标 `source_conflict`。
- `sources[]`：实际执行的检索记录 `{engine,query,url,retrieved_at,count,note}`。
- `uncertainty[]`、`pending_human[]`、`errors[]`、`degradations[]`、`limitations[]`、`change_log[]`、`runtime{}`、`budget{}`。

除 `result.json` 外，可附 `report.md`（finalize 生成）。status 诚实：材料不足/无法判断标 `insufficient_evidence`，运行失败标 `input_error`，不编造文献、号码、状态或分数。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

**总原则：前 3 分钟（180 秒）内必须先落盘一版合法的 partial result.json，之后只做增量改进；每步若已有合法产物就跳过重复写盘。** 阶段预算写入 `budget.phases`，用 `python3 scripts/main.py budget` 随时查看剩余时间/请求。

| 阶段 | 时间窗 | 请求上限 | 是否必存 | 交付物 |
|---|---|---|---|---|
| P0 scaffold | 0–180s | ≤4 | **必存** | partial result.json 骨架（3 分钟内） |
| P1 scope | 180–300s | ≤6 | 否 | research_question / retrieval_scope / authorization |
| P2 numbers | 300–540s | ≤20 | 否 | patent_records / families / legal_status |
| P3 topic | 540–780s | ≤24 | 否 | literature / evidence / sources |
| P4 finalize | 780–900s | ≤8 | 必存 | final result.json + report.md + VALID |

各阶段预算之和 62 ≤ 64，预留 2 次请求作冲突核验/异常处理余量。每阶段结束执行 `checkpoint` 增量落盘；逼近超时立即 `finalize` 并标 `partial`。

**Step 0 — 读输入并落盘骨架（0–180s，≤4 次请求）**
执行 `python3 scripts/main.py scaffold`（默认 `--input /input --output /app/output --work /app/work`，公共参数可放子命令后）。输出 `result.json`（status=partial）+ `/app/work/state.json` + `input_inventory`。scaffold 失败按 §6 手工写最小 JSON。**180 秒护栏：此时 result.json 必须已存在且能被 `validate` 判为 VALID。**

**Step 1 — 明确问题与检索范围（180–300s，≤6 次请求）**
读 case.json/task.md，判定"号码定位"（给定公开/申请/优先权号找专利）还是"主题检索"（关键词/分类号/申请人找文献）。回填 `research_question/retrieval_scope`；识别目标数据源可用性并写 `authorization` 绿/黄/红。

**Step 2 — 号码规范化与同族归并（300–540s，≤20 次请求）**
对每个号码跑 `python3 scripts/main.py normalize --text "<号码>"`；从扫描件/OCR 文本跑 `python3 scripts/main.py extract --file <文本>` 找候选再逐条核对。把 records 写 JSON 后跑 `python3 scripts/main.py family --file records.json` 自动归并，**结果标 `observed` 且 `exhaustive:false`**。核对权利要求逐字引用与法律状态来源（官方登记优先于转载页）。

**Step 3 — 主题检索与证据对齐（540–780s，≤24 次请求）**
主题检索优先 paper_search/web_search（官方登记页、出版商、预印本），逐条写 `sources` 与 `evidence`；号码核验优先官方库（USPTO/Espacenet/PATENTSCOPE/国家局）。冲突并列记录，不静默择一。

**Step 4 — 去重、补缺口、收尾（780–900s，≤8 次请求）**
执行 `python3 scripts/main.py finalize`：校验契约、补 `runtime`、生成 `report.md`；补 `uncertainty/pending_human/errors/degradations/limitations`；确认 status 诚实。空结果写 `insufficient_evidence`，不重开大检索；逼近超时立即落盘并标 partial。

**预算护栏**：单响应 ≤16384 tokens（含推理）；每条结论必须有 locator+quote；检索失败用状态字段说明，不补造文献。

## 5. 自检清单

逐条勾选，详见 `references/selfcheck-checklist.md`。收尾前必跑：

```bash
python3 -B scripts/main.py --help        # 退出码 0
python3 -B scripts/main.py scaffold      # 早落盘（3 分钟内）
python3 -B scripts/main.py finalize      # 校验 + 补 runtime + 生成 report.md
python3 -B scripts/main.py validate      # 必须打印 VALID
python3 -B scripts/selftest.py           # 27 项自检全 PASS
```

- [ ] frontmatter 齐备（name/description/version）
- [ ] result.json 含 case_id + status + 主产物 + sources + uncertainty + pending_human + change_log
- [ ] output_contract 全部字段原样回填，无改名/删键
- [ ] 3 分钟内先落盘 partial 骨架，之后只做增量改进
- [ ] 每条关键结论有 locator + 逐字 quote；冲突标 source_conflict
- [ ] 缺数据/缺软件如实标 limitations，不编造
- [ ] 空输入/坏 JSON/缺输入三种异常各跑一次，退出码 0 且产物合法
- [ ] 号码规范化、同族归并、法律状态、授权边界详见 `references/normalization-and-family.md`

## 6. 降级策略（不允许编造）

- 空输入/无材料 → `insufficient_evidence`，`pending_human` 列缺口，退出码 0。
- 坏 JSON/坏编码 → 记入 `errors/degradations`，继续处理其余材料。
- 检索无命中/限流/鉴权失败 → 如实写 `sources` 与 `limitations`，标 `partial`，不补造文献。
- 法律状态冲突/过期 → 双方证据并列标 `source_conflict`，写 `staleness_risk` 与 `pending_human`；官方状态未知就写 `unknown`，不推断"有效"。
- 字段缺失/OCR 边界不明 → `uncertainty`（`field_missing`/`ocr_uncertain`/`family_scope_unclear`），写 `not_reported`，不硬猜。
- 受限库/付费墙 → 标 `degradations` 说明不可用与降级方案，不尝试登录或绕过。
- 缺软件/接口不可用 → 标 `input_error` 或 `partial`，说明缺失项，不伪造运行记录或分数。
- 写盘失败 → 降级为最小合法 result.json，进程不崩溃。

## 7. 脚本

均在 Skill 根目录用相对路径调用，纯标准库，可用 `--help` 查看：

- `scripts/main.py`：`scaffold / normalize / extract / family / checkpoint / budget / finalize / validate` 八子命令。
- `scripts/patentlib.py`：被 main.py 引用的工具库（容错读取、号码规范化、号码抽取、同族归并、骨架与校验）。
- `scripts/selftest.py`：自检脚本（27 项断言，覆盖异常输入与契约回填）。

最小调用示例（示意，非正式案例结果）：

```bash
python3 scripts/main.py scaffold --input /input --output /app/output --work /app/work
python3 scripts/main.py normalize --text "US 10,000,000 B2"
python3 scripts/main.py family --file /app/work/records.json
python3 scripts/main.py budget
python3 scripts/main.py checkpoint --note "P2 完成" --requests 20
python3 scripts/main.py finalize --status ok
```

示意输出 `result.json`：`{"case_id":"case-demo","status":"ok","patent_records":[...],"families":[...],"legal_status":[...],"literature":[...],"evidence":[...],"sources":[...],"uncertainty":[...],"change_log":[...],"runtime":{...},"budget":{...}}`（仅接口示例，不冒充正式案例答案）。

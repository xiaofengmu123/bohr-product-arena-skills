---
name: patent-intel-search
description: 专利与技术文献检索 Skill：界定检索范围，规范化公开号/申请号/优先权号，按共用优先权归并同族并标注口径，核验法律状态与权利范围，检索同主题技术文献，逐条保留来源与相关性依据，交付可复核的 /app/output/result.json。适用于专利查新、号码定位、同族归并、法律状态核验与技术情报检索。
version: 1.0.0
---

# 专利与技术文献检索 Skill

## 1. 角色与何时使用

本 Skill 把“专利/技术文献检索”沉淀为可重复、可复核的流程。核心不是查多少库，而是：结果只来自有授权的来源并标注库名、版本与查询日期；公开号/申请号/授权号/同族/法律状态对得上官方登记口径；检索与核验过程可被他人按记录复跑。

适用：专利号/族定位与规范化、主题检索式构造、法律状态核验、同主题技术文献发现、授权边界分级。不适用：专利撰写、侵权判定、自由实施分析结论、专利价值评估；不绕过付费墙、不尝试登录受限库、不展示受限库原文。

一次检索须回答五件事，逐项落字段并给证据：①该专利保护什么（权利要求逐字引用，不越界概括）；②与同族成员什么关系（simple/extended/observed 口径分开）；③现在是否有效（状态+生效日+来源+查询日期+滞后风险）；④同主题还有谁在做（区分专利文献与技术文献的检索逻辑）；⑤这些信息是否有权访问与展示（绿/黄/红分级）。

## 2. 输入契约（运行时）

- 原始输入在 `/input/`：`case.json`（含 `case_id`、任务说明、可能的 `output_contract`）与 `task.md`，另可能有 `materials/`、`notes.md`、PDF 等。
- **扫描而非假设**：不假设固定文件名/键名；先执行 `scripts/main.py scaffold` 发现输入。PDF 由平台解析工具提供文本层，扫描件 OCR 可能粘连；读实际文本，不依据标签脑补内容。
- 文本按 `utf-8-sig` 容错读取；单文件 2MB 上限，超限跳过并记录；坏 JSON/坏编码降级不崩溃。
- 工作目录 `/app/work`；最终产物与可编辑工件写到 `/app/output/`。
- 号码类查询先定位再核验；主题类查询用分类号/申请人/引证组合检索。

## 3. 输出契约

必须产出 `/app/output/result.json`。若 `case.json` 提供 `output_contract`，其全部字段**原样回填**（缺值给 `null`/`[]`，不改名、不删未知键）；无契约时用默认 schema，Agent 在此基础上增量填充：

- `case_id`（非空；缺则 `unknown` 并写 uncertainty）与 `status`：`ok | partial | insufficient_evidence | input_error`。
- `research_question`、`retrieval_scope`：研究问题与检索范围。
- `patent_records[]`：每件目标专利，含 `record_id`、公开号/申请号/优先权号（raw + normalized）、`number_type`、权利要求逐字引用、来源 `locator`+`quote`、`confidence`。
- `families[]`：成员、`family_type`（simple/extended/observed）、`exhaustive:false`、归并依据与证据。
- `legal_status[]`：`status`、`effective_date`、`source`、`as_of`、`staleness_risk`、`confidence`。
- `literature[]`：`rank/title/authors/year/venue/doi/source/relevance_rationale/evidence`。
- `authorization[]`：`{source, license_basis, tier, displayable_fields, retention}`。
- `evidence[]`：每条结论的 `locator` + 逐字 `quote`；冲突标 `source_conflict`。
- `sources[]`：`{engine, query, url, retrieved_at, count, note}`。
- `uncertainty[]`、`pending_human[]`、`errors[]`、`degradations[]`、`limitations[]`、`change_log[]`、`runtime{}`。

除 `result.json` 外可附 `report.md`（finalize 生成）。status 要诚实：材料不足/无法判断标 `insufficient_evidence`，运行失败标 `input_error`，不编造文献、号码、状态或分数。字段口径与同族规则详见 `references/field-spec.md`。

## 4. 执行流程（总预算 900 秒 / 64 次请求）

**前 3 分钟内必须先落盘一版合法 partial result.json**，之后只做增量改进；每步已有合法产物就跳过重复写盘。单响应 ≤16384 tokens。

- **Step 0 骨架（0–60s，≤4 请求）**：执行 `python3 scripts/main.py scaffold`（默认 `--input /input --output /app/output --work /app/work`），写 `result.json`(partial) + `state.json` + 输入清单；失败则按 §6 手写最小 JSON。
- **Step 1 界定问题（60–180s，≤6 请求）**：判读“号码定位”还是“主题检索”；回填 `research_question/retrieval_scope`；识别数据源可用性并写 `authorization` 绿/黄/红。
- **Step 2 号码规范化与同族（180–540s，≤22 请求）**：逐号跑 `python3 scripts/main.py normalize --text "<号码>"`；扫描件跑 `extract --file` 找候选再核对；records 落 JSON 后跑 `python3 scripts/main.py family --file` 归并，结果标 `observed` 且 `exhaustive:false`；核对权利要求逐字引用与法律状态来源。
- **Step 3 主题检索与证据（540–780s，≤24 请求）**：优先 paper_search/web_search（官方登记页、出版商、预印本），逐条写 `sources` 与 `evidence`；号码核验优先官方库（USPTO/Espacenet/PATENTSCOPE/国家局）。冲突并列记录，不静默择一。
- **Step 4 收尾（780–900s，≤8 请求）**：跑 `python3 scripts/main.py finalize` 校验契约、补 `runtime`、生成 `report.md`；补缺口字段；确认 status 诚实。空结果写 `insufficient_evidence`，逼近超时立即落盘标 partial。

## 5. 自检清单（逐条勾选）

提交/收尾前逐条确认，并运行 `python3 scripts/selftest.py`（退出码 0 为通过）：

- [ ] `SKILL.md` frontmatter 含 name/description/version
- [ ] `result.json` 含 `case_id`、`status` 与 output_contract 全部字段
- [ ] 900 秒/64 请求预算已写明，且规定前 3 分钟落盘 partial
- [ ] 每条关键结论有 `locator` + 逐字 `quote`
- [ ] 号码规范化/同族归并/法律状态字段口径与官方登记一致
- [ ] 空结果、冲突、权限/接口异常已写 `uncertainty/pending_human/limitations`
- [ ] 缺数据/缺软件如实标注，未编造文献、号码、状态、分数
- [ ] `python3 scripts/main.py --help` 退出码 0
- [ ] `scaffold → finalize → validate` 全链路通过，validate 打印 `VALID`
- [ ] 空输入/坏 JSON/缺输入三种异常各跑一次，退出码 0 且产物合法

## 6. 降级策略（不许编造）

- 空输入/无材料 → `insufficient_evidence`，`pending_human` 列缺口，退出码 0。
- 坏 JSON/坏编码 → 记入 `errors/degradations`，继续处理其余材料。
- 检索无命中/限流/鉴权失败 → 如实写 `sources` 与 `limitations`，标 `partial`，不补造文献。
- 法律状态冲突/过期 → 双方证据并列标 `source_conflict`，写 `staleness_risk` 与 `pending_human`；官方状态未知写 `unknown`，不推断“有效”。
- 字段缺失/OCR 边界不明 → `uncertainty`（`field_missing/ocr_uncertain/family_scope_unclear`），写 `not_reported`，不硬猜。
- 受限库/付费墙 → 标 `degradations` 说明不可用与降级方案，不尝试登录或绕过。
- 缺软件/接口不可用 → 标 `input_error` 或 `partial`，说明缺失项，不伪造运行记录或分数。
- 写盘失败 → 降级为最小合法 result.json，进程不崩溃。

## 7. 脚本

均在 Skill 根目录用相对路径调用，纯标准库，可用 `--help` 查看：

- `scripts/main.py`：`scaffold / normalize / extract / family / finalize / validate` 六子命令。
- `scripts/patentlib.py`：核心库（容错读取、号码规范化/抽取、同族归并、骨架与校验）。
- `scripts/selftest.py`：自检脚本，退出码 0 表示通过。

最小调用示例（示意，非正式案例结果）：

```bash
python3 scripts/main.py scaffold
python3 scripts/main.py normalize --text "US 12,345,678 B2"
python3 scripts/main.py family --file /app/work/records.json
python3 scripts/main.py finalize
python3 scripts/main.py validate
```

示意 `result.json` 骨架：`{"case_id":"case-demo","status":"ok","patent_records":[...],"families":[...],"legal_status":[...],"literature":[...],"evidence":[...],"sources":[...],"uncertainty":[...],"change_log":[...],"runtime":{...}}`（仅接口示例，不冒充正式案例答案）。

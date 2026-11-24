---
name: crosslingual-literature-search
description: 跨语言科研文献检索 Skill。从研究问题或已知条目出发确定源语言与目标语种，建立可复核术语表，生成逐语种查询，取回并筛选文献，判定每条命中的语言与版本（原文/全文译文/双语刊/仅摘要/精确重复），对同一论文的多语版本去重，并为每条结果保留来源定位与逐字证据。用于需要找回非英语原始研究、已知条目跨语种对位、术语对齐可核验、版本可追溯的科研检索任务。
version: 1.1.0
---

# 跨语言文献检索

## 1. 角色与何时使用
本 Skill 面向"跨语言科研文献检索"：给定研究问题或已知条目，找回相关文献，重点覆盖非英语原始研究，并说明每条命中的语言、版本与相关性依据。适用：多语种检索、术语对齐、原文/译文/仅摘要版本判定、同论文多语版本去重。不适用：整篇翻译、写作润色、未经授权的跨境数据抓取。

本版侧重点：**精简与可读**——文件数压到 5 个、脚本仅标准库、无冗余文档；用"先落盘骨架、再增量补丁"保证任何中断都留下合法部分结果。

## 2. 输入契约
运行时原始输入固定位于 `/input/`：
- `/input/case.json`：案例任务、输入约束、可能的 `output_contract`、材料清单；
- `/input/task.md`：题目总说明与评分维度；
- `/input/materials/`（若存在）：PDF 原文/译文等事实依据。

发现而非假设：不假设固定文件名或键名，先用 `init` 自动发现；`case.json` 含 `output_contract`（或 `outputContract`）时其全部字段必须原样回填；文本按 UTF-8 容错读取（BOM、坏编码降级）；PDF 用平台 pdftotext 解析实际文件，必要时 OCR 兜底；单文件超 2MB 跳过并记入 `degradations`。工作目录 `/app/work/`，最终产物写 `/app/output/`。

## 3. 输出契约
必须产出 `/app/output/result.json`，包含：
- `case_id`（无法解析时 `status=input_error` 并说明）；
- `output_contract` 全部字段逐一回填，缺值给 `null`/`[]`，不改名、不删未知键；
- 标准字段：`status`、`sources`、`uncertainty`、`change_log`，以及 `errors`、`degradations`、`pending_human`、`runtime`。

`status` 取值：`ok`（完整完成）、`partial`（部分完成并如实说明）、`insufficient_evidence`（材料不足）、`input_error`（输入缺失或损坏）。

默认主结果字段（case 提供 contract 时以 contract 为准并补齐下列语义字段）：
- `research_question`：澄清后问题、源语言、目标语种、检索范围、是否已知条目；
- `terminology_table`：术语对齐表（源词、语种、变体、类型、依据、验证方式）；
- `queries`：逐语种查询式，含翻译服务与版本、原文-译文对照；
- `works`：已知条目/论文的分组，列出同一作品的全部语言版本；
- `results`：候选文献（标题、作者、年份、期刊/会议、DOI/URL、语言、版本、来源、相关性依据）；
- `evidence_map`：关键结论/数值/命中的定位（locator）+ 逐字 quote；
- `dedup`：同论文多语版本去重判定与依据；
- `missing` / `conflicts`：遗漏、无结果、冲突、权限问题逐条说明。

每条 `results[]` 至少含：`title`、`doi`/`url`、`language`、`version`、`source`、`locator`、`quote`、`relevance_basis`；缺证标 `pending_human`。可编辑副产物（`terminology_table.md`、`queries.md` 等）与 `result.json` 一并放 `/app/output/`。

## 4. 语言与版本判定词汇
本 Skill 面向的固定案例为**双向已知条目对位**：原始正文为葡萄牙语或英语，含出版方全文翻译、仅英文摘要、以及显式精确重复。版本取值：`original`（原文正文）、`full_translation`（出版方全文译文）、`bilingual`（双语刊）、`abstract_only`（正文一种语言、摘要另一种语言）、`duplicate`（同一版本精确重复）、`undetermined`（证据不足）。判定依据必写：正文语言、摘要语言、双语刊声明、出版方翻译声明、DOI/标题归一后的一致性。语言先由 `scripts/main.py lang` 给出启发式初判（`pt`/`en`/`undetermined`），再由人工/模型按证据复核。

## 5. 执行流程（总预算 900 秒、64 次模型请求、16384 tokens/响应）
第 0 步必须最先执行，立即落盘结果骨架：

```bash
python3 -B scripts/main.py init --input /input --output /app/output --work /app/work
```

该命令发现输入、解析 `case_id` 与契约，原子写入合法 `result.json`（全部契约字段、`status:"partial"` 初值、空结果列表）。**必须在 3 分钟（180 秒）内完成**；之后只做增量改进，每步用补丁写回：

```bash
python3 -B scripts/main.py patch --output /app/output --patch <补丁>.json
```

| 步骤 | 时间窗 | 输入 | 输出 |
|---|---|---|---|
| 0 初始化 | 0–60s | /input 目录 | result.json 骨架 + work/state.json |
| 1 读题定范围 | 0–180s | case.json、task.md | research_question、源/目标语种、范围 |
| 2 术语对齐 | 60–300s | 问题、材料、术语 | terminology_table（逐语种） |
| 3 逐语种检索 | 120–600s | queries | 每条命中 + 来源 + 逐字证据 |
| 4 语言/版本识别 | 300–720s | results | 每条 language/version + dedup |
| 5 对齐与误译复核 | 480–840s | 命中与原文 | evidence_map、conflicts、missing |
| 6 收尾自检 | 720–900s | 全部产物 | validate 通过 + change_log + 最终 status |

每步准则：
- 翻译与查询必须记录翻译服务与版本，保留原文-译文对照；未记录来源的译文标 `unverified_translation`；
- 检索用 web_search / paper_search，逐语种分别构造查询，不得只用母语；
- 每条命中必须有 locator（URL/DOI/材料段落定位）与逐字 quote，缺失标 `pending_human`；
- 语言与版本判定写依据；冲突信息两个都记录并标 `source_conflict`；
- 每步结束调用 `python3 -B scripts/main.py log --action <步骤> --detail <摘要>` 追加变更记录。

## 6. 自检清单（逐条勾选，对应评分维度）
- [ ] `case_id` 已解析写入；`output_contract` 全部字段回填，缺值 `null`/`[]`，未改名、未删键；
- [ ] `status` 诚实（ok/partial/insufficient_evidence/input_error），未编造数据；
- [ ] 研究问题、源语言、目标语种、检索范围已明确；
- [ ] `terminology_table` 存在，每条含 type、basis、verification；
- [ ] 逐语种查询已记录，含翻译服务与版本、原文-译文对照；
- [ ] 每条结果有 locator + 逐字 quote，语言与版本标注清楚，关键非英语文献被覆盖；
- [ ] 同论文多语版本已去重并记录依据（含精确重复 `duplicate`）；
- [ ] `missing`/`conflicts`/权限与接口异常已如实说明；
- [ ] `result.json` 是合法 UTF-8 JSON，`scripts/main.py validate` 无 issues；
- [ ] 骨架在 3 分钟内落盘；同一输入重复运行结果一致（无随机性）。

## 7. 降级策略（不得编造）
- 缺 `case.json`/`task.md` → `status=input_error`，列出缺口，退出码 0；
- 坏 JSON/坏编码 → 记入 `errors`/`degradations`，继续处理其余材料；
- 网络/检索工具不可用 → `insufficient_evidence` 或 `partial`，明确已完成与未完成；
- 翻译服务失败/限额 → 回退纯术语表查询，未验证译文标 `unverified_translation`；
- 语种无法确定 → `language:"undetermined"`，写入 `uncertainty`；
- 原文不可得或仅摘要 → 版本标 `abstract_only`/`undetermined`，不冒充全文；
- 材料缺失关键数值 → `not_reported`/`pending_human`，不补造；
- 任何失败留下合法最小产物；同一命令失败最多重试 2 次，之后降级。

## 8. 脚本命令
- `python3 -B scripts/main.py init`：发现输入、写骨架；
- `python3 -B scripts/main.py patch --patch p.json`：深合并补丁（列表整体替换、字典递归合并）；
- `python3 -B scripts/main.py log --action A --detail D`：追加 change_log；
- `python3 -B scripts/main.py lang --text "..."`：启发式语言识别；
- `python3 -B scripts/main.py norm --doi ... --title ...`：DOI/标题归一去重键；
- `python3 -B scripts/main.py validate`：校验契约字段与 JSON 合法性；
- `python3 -B scripts/main.py selftest`：离线自检（空输入/坏 JSON/契约回填/确定性/语言/去重）。

方法细则见 `references/method.md`；脚本仅依赖 Python 标准库，路径用相对路径。

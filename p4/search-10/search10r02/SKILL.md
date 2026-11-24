---
name: crosslingual-knownitem-search
description: 跨语言"已知项"科研文献检索 Skill。给定一个研究问题与一篇已知论文的部分标识（DOI/作者/标题片段/语种），找回该论文的原文与译文版本，判定每条命中的正文语言与版本类型（原文/出版方全文译文/仅摘要/双语刊），对同一论文的多语版本去重，并为每条命中保留来源定位与逐字证据。用于需要双向找回非英语原始研究、术语对齐可核验、版本可追溯的科研检索任务。
version: 1.0.0
---

# 跨语言已知项文献检索

## 1. 角色与何时使用
本 Skill 解决"已知项（known-item）跨语言找回"问题：研究问题指向一篇或多篇**可被部分标识**的真实论文（DOI、作者、年份、标题片段、语种声明），任务要求在原文之外，找回其另一语种版本并说清二者关系。

适用场景：
- 需要双向检索（如葡萄牙语↔英语），把非英语原始研究、本地期刊与区域会议论文一并找回；
- 需要判断每条命中的**正文语言**与**版本类型**，并对同一论文的多语版本去重；
- 需要保留术语对齐表、逐语种查询式与翻译服务/版本，结果可被第三方逐条复核。

不适用：整篇翻译、写作润色、未经授权的跨境数据抓取、无证据要求的一次性问答。

## 2. 输入契约（先发现，再假设）
运行时原始输入固定位于 `/input/`：
- `/input/case.json`：案例任务、输入约束、可能的 `output_contract`、材料清单；
- `/input/task.md`：题目总说明与评分维度；
- `/input/materials/`（若存在）：科研材料、PDF 原文/译文等事实依据。

发现而非假设：
- 不假设固定文件名或键名，用 `scripts/main.py init` 自动发现；
- `case.json` 若含 `output_contract`（或 `outputContract`），其全部字段必须原样回填，缺值给 `null`/`[]`，不改名、不删未知键；
- 文本统一按 UTF-8 容错读取（BOM、坏编码降级，不崩溃）；
- PDF 用平台 pdftotext 解析**实际文件**，不依据文件名或标签假定内容，必要时 OCR 兜底；
- 单文件超 2MB 时跳过并记入 `degradations`。

工作目录 `/app/work/`，最终产物写 `/app/output/`。

## 3. 输出契约
必须产出 `/app/output/result.json`，包含：
- `case_id`（从输入解析；无法解析时 `status=input_error` 并说明）；
- `output_contract` 的全部字段，逐一回填，缺值给 `null`/`[]`，不改名、不删未知键；
- 附加标准字段：`status`、`sources`、`uncertainty`、`change_log`，以及 `errors`、`degradations`、`pending_human`、`runtime`。

`status` 取值：`ok`（完整完成）、`partial`（部分完成，如实说明已完成与未完成）、`insufficient_evidence`（材料不足）、`input_error`（输入缺失或损坏）。

默认主结果字段（若 case 提供 contract 则以 contract 为准，并补齐下列语义字段）：
- `research_question`：澄清后的问题、源语言、目标语种、检索范围与已知项种子（DOI/作者/标题片段/年份）；
- `terminology_table`：术语对齐表（源词、语种、变体、类型、依据、验证方式）；
- `queries`：逐语种查询式，含翻译服务与版本、原文-译文对照；
- `results`：候选文献（标题、作者、年份、期刊/会议、DOI/URL、正文语言、版本类型、来源、相关性依据）；
- `evidence_map`：每条关键结论/数值/命中的定位（locator）+ 逐字 quote；
- `dedup`：同论文多语版本去重判定与依据；
- `missing` / `conflicts`：遗漏、无结果、冲突、权限问题，逐条说明。

可编辑副产物（如 `terminology_table.md`、`queries.md`）与 `result.json` 一并放 `/app/output/`。

## 4. 执行流程：阶段预算与早落盘（总预算 900 秒 / 64 次模型请求）
**核心纪律：先落盘合法的 partial 骨架，再增量改进；任何后续异常都不会留下半截 JSON。**

第 0 步必须最先执行，立即落盘结果骨架（**必须在第 3 分钟 / 180 秒内完成**）：

```bash
python3 -B scripts/main.py init --input /input --output /app/output --work /app/work
```

该命令发现输入、解析 `case_id` 与契约、计算阶段截止时间，原子写入合法 `result.json` 骨架（全部契约字段、`status:"partial"` 初值、空结果列表）与 `work/state.json`（含阶段计划与已用时间）。

之后每步把新证据 patch 回 `result.json`：

```bash
python3 -B scripts/main.py patch --output /app/output --work /app/work --patch <补丁文件>.json
```

| 阶段 | 时间窗 | 输入 | 输出 | 完成后动作 |
|---|---|---|---|---|
| 0 初始化 | 0–60s | /input 目录 | result.json 骨架 + work/state.json | 立即 patch 一次 `research_question` 草稿 |
| 1 读题定范围 | 0–180s | case.json、task.md | research_question、源/目标语种、范围、已知项种子 | patch `research_question` |
| 2 术语对齐 | 60–300s | 研究问题、材料、术语 | terminology_table（逐语种） | patch `terminology_table` |
| 3 逐语种检索 | 120–600s | queries | 每条命中 + 来源 + 逐字证据 | patch `queries` 与 `results` |
| 4 语言/版本识别 | 300–720s | results | 每条 language/version + dedup 判定 | patch `results` 与 `dedup` |
| 5 对齐与误译复核 | 480–840s | 命中与原文 | evidence_map、conflicts、missing | patch `evidence_map`/`conflicts`/`missing` |
| 6 收尾自检 | 720–900s | 全部产物 | validate 通过 + change_log + 最终 status | patch 最终 `status`，运行 validate |

阶段纪律：
- 每步先 `scripts/main.py phase --work /app/work` 查看当前阶段与剩余时间/请求预算；**超时未产出时按第 6 节降级，不空转、不编造**。
- 翻译与查询必须记录所用翻译服务与版本，保留原文-译文对照；未记录来源的译文不得当作已核对。
- 检索用 web_search / paper_search，逐语种分别构造查询，不得只用母语。
- 每条命中必须有 locator（URL/DOI/材料段落定位）与逐字 quote，缺失标 `pending_human`。
- 语言与版本判定写依据（正文语言、摘要语言、双语刊声明、出版方翻译声明）。
- 冲突信息不隐藏：同一指标来源不一致时两个都记录并标 `source_conflict`。

### 4.1 已知项双向检索法（本 Skill 的核心方法）
本任务多为"已知项"：case 中通常给出足以定位一篇真实论文的**种子标识**（DOI、作者、年份、标题片段、语种声明）。按此顺序工作，优先把"已知项"坐实，再补另一语种：

1. **提取种子**：从 research_question 中抽取 DOI、arXiv/PMID、作者、年份、标题片段、声明的语种与版本；缺失标 `pending_human`。
2. **定语言对与方向**：把语种归一化（如 `pt`/`por`→`pt`，`en`/`eng`→`en`），确定双向对与检索方向；无法确定标 `undetermined`。
3. **逐语种查询**：为每种语言分别构造查询，术语用术语表对齐（学科用词优先于字面直译），记录翻译服务与版本及原文-译文对照。
4. **坐实已知项**：先用 DOI/作者/标题对每条命中做精确匹配，把"这篇就是 case 指的那篇"与"这只是同主题文献"分开；精确匹配项标 `known_item_match:true`。
5. **判版本**：对照命中与种子，判定每条为 `original`、`publisher_translation`、`abstract_only`、`bilingual` 或 `undetermined`；写依据，不冒充全文。
6. **去重**：以"论文作品"为单位合并多语版本（DOI 归一化 + 标题/作者指纹），去重依据写入 `dedup`，声明语种都应有有效结果。

## 5. 自检清单（对应评分维度，逐条勾选）
- [ ] `case_id` 已解析并写入；
- [ ] `output_contract` 全部字段已回填，缺值用 `null`/`[]`，未改名、未删键；
- [ ] `status` 诚实（ok/partial/insufficient_evidence/input_error），未编造数据；
- [ ] 研究问题、源语言、目标语种、检索范围、已知项种子已明确；
- [ ] `terminology_table` 存在，每条含 type、basis、verification；
- [ ] 逐语种查询已记录，含翻译服务与版本、原文-译文对照；
- [ ] 每条结果有来源定位与逐字证据，关键非英语文献被覆盖；
- [ ] 每条命中标注正文语言与版本类型，同一论文多语版本已去重并记录依据；
- [ ] `missing`/`conflicts`/权限与接口异常已如实说明；
- [ ] 术语表、查询式、语种判断依据可被第三方独立复核；
- [ ] `result.json` 是合法 UTF-8 JSON，`scripts/main.py validate` 无 issues；
- [ ] 骨架在 180 秒内落盘；同一输入重复运行结果一致（无随机性）。

## 6. 降级策略（不得编造）
- 缺 `case.json`/`task.md` → `status=input_error`，列出缺口，退出码 0；
- 坏 JSON/坏编码 → 记入 `errors`/`degradations`，继续处理其余材料；
- 网络或检索工具不可用 → `insufficient_evidence` 或 `partial`，明确已完成与未完成；
- 翻译服务失败/限额 → 回退纯术语表查询，未验证译文标 `unverified_translation`；
- 语种无法确定 → `language:"undetermined"`，写入 `uncertainty`；
- 原文不可得或仅摘要 → 版本标 `abstract_only` 或 `undetermined`，不冒充全文；
- 材料缺失关键数值 → `not_reported` 或 `pending_human`，不补造；
- 任一阶段超时 → 用已有证据 patch 一版 partial 结果并标 `partial`，不阻塞收尾；
- 任何失败都要留下合法的最小产物，不让进程崩溃；同一命令失败最多重试 2 次，之后降级。

## 7. 脚本命令（仅标准库）
- `python3 -B scripts/main.py init ...`：发现输入、写骨架与阶段计划；
- `python3 -B scripts/main.py phase --work /app/work`：查看当前阶段与剩余预算；
- `python3 -B scripts/main.py patch --patch p.json`：深合并补丁（列表整体替换、字典递归合并）；
- `python3 -B scripts/main.py validate`：校验契约字段与 JSON 合法性，输出缺失清单；
- `python3 -B scripts/main.py status`：摘要当前进度与 change_log。

方法规则见 `references/method.md`，逐条自检见 `references/checklist.md`，本地自测用 `scripts/selftest.py`。

---
name: crosslingual-literature-search
description: 跨语言科研文献检索 Skill。用于已知项检索（尤其葡萄牙语↔英语双向）与开放式科研检索：从研究问题确定源/目标语种，建立可复核术语表，生成逐语种查询，取回并筛选文献，判定每条命中的语言与版本（原文/出版商全译/仅摘要/重复），对同一论文多语版本去重，为每条结果保留来源定位与逐字证据；脚本支持原子落盘、确定性指纹与自检。适用需要找回非英语原始研究、术语对齐可核验、版本可追溯的科研检索任务。
version: 1.0.0
---

# 跨语言文献检索

## 1. 角色与何时使用
本 Skill 面向“跨语言科研文献检索”：给定研究问题，找到相关文献，尤其找回非英语原始研究、本地期刊与区域会议论文，并说明每条命中的语言、版本与相关性依据。

适用：研究问题涉及多个语种或明确要求非英语文献；需要术语对齐（同义词、缩写、上下位、音译、本地译名）且结果可人工复核；需要区分原文、译文、双语刊、仅摘要版本并对同一论文多语版本去重；**已知项检索**（用户已知某篇具体论文，需找回其不同语种/版本，本平台固定案例即此类）。

不适用：整篇翻译、写作润色、未经授权的跨境数据抓取、无证据要求的一次性问答。

**本题固定案例边界**：3 个案例为葡萄牙语（pt）↔英语（en）双向的 known-item 检索，覆盖 3 篇真实论文 / 6 种视图：葡语原文正文、英语原文正文、出版商全译、仅英语摘要、以及显式重复记录。必须保留 source / DOI / version / body language / attribution。不做实时覆盖率、不引入未见语种、不虚构新研究结论。

## 2. 输入契约
运行时原始输入固定位于 `/input/`：
- `/input/case.json`：案例任务、输入约束、可能的 `output_contract`、材料清单；
- `/input/task.md`：题目总说明与评分维度；
- `/input/materials/`（若存在）：论文 PDF 原文/译文等事实依据。

发现而非假设：不假设固定文件名/键名，用 `scripts/main.py init` 自动发现；`case.json` 若含 `output_contract`（或 `outputContract`），其全部字段必须原样回填；文本统一按 UTF-8 容错读取（BOM、坏编码降级，不崩溃）；PDF 用平台 pdftotext 解析实际文件，不依据文件名/标签假定内容；单文件超 2MB 时跳过并记入 `degradations`。

工作目录 `/app/work/`，最终产物写 `/app/output/`。

## 3. 输出契约
必须产出 `/app/output/result.json`，包含：
- `case_id`（从输入解析；无法解析时 `status=input_error` 并说明）；
- `output_contract` 的全部字段逐一回填，缺值给 `null`/`[]`，不改名、不删未知键；
- 附加标准字段：`status`、`sources`、`uncertainty`、`change_log`，以及 `errors`、`degradations`、`pending_human`、`runtime`、`determinism_fingerprint`。

`status` 取值：`ok`（完整完成）、`partial`（部分完成，如实说明已完成与未完成）、`insufficient_evidence`（材料不足）、`input_error`（输入缺失或损坏）。

主结果字段（若 case 提供 contract 则以 contract 为准，并补齐下列语义字段）：
- `research_question`：澄清后的问题、源语言、目标语种、检索范围、`known_item`（是否已知项检索）；
- `terminology_table`：术语对齐表（源词、语种、变体、类型、依据、验证方式）；
- `queries`：逐语种查询式，含翻译服务与版本、原文-译文对照；
- `results`：候选文献（标题、作者、年份、期刊/会议、DOI/URL、`body_language`、`version`、来源、相关性依据）；
- `evidence_map`：每条关键结论/数值/命中的定位（locator）+ 逐字 quote；
- `dedup`：同论文多语版本去重判定与依据；
- `missing` / `conflicts`：遗漏、无结果、冲突、权限问题逐条说明。

**语言与版本标注（硬规则）**：每条命中必须写 `body_language`（正文语言）与 `version`。`version` 枚举：`original`（正文即该论文原始语种）、`publisher_translation`（出版商全译，非摘要）、`abstract_only`（该语种仅有摘要，正文为另一语种）、`duplicate`（与另一条记录完全重复）、`undetermined`（无法判定）。判定依据写入 `evidence_map`（正文语言、摘要语言、双语刊声明、出版方翻译声明）。

可编辑副产物（如 `terminology_table.md`、`queries.md`）与 `result.json` 一并放 `/app/output/`。

## 4. 执行流程（总预算 900 秒、64 次模型请求）
**第 0 步必须最先执行，立即落盘结果骨架**，之后任何异常都不留不完整产物：

```bash
python3 -B scripts/main.py init --input /input --output /app/output --work /app/work
```

该命令发现输入、解析 `case_id` 与契约、原子写入合法 `result.json` 骨架（全部契约字段、`status:"partial"` 初值、空结果列表、初始指纹）。**必须在第 3 分钟（180 秒）内完成。** 之后只做增量改进，每步把新证据 patch 回 `result.json`：

```bash
python3 -B scripts/main.py patch --output /app/output --work /app/work --patch <补丁文件>.json
```

| 步骤 | 时间窗 | 输入 | 输出 |
|---|---|---|---|
| 0 初始化 | 0–60s | /input 目录 | result.json 骨架 + work/state.json |
| 1 读题定范围 | 0–180s | case.json、task.md | research_question、源/目标语种、known_item、范围 |
| 2 术语对齐 | 60–300s | 研究问题、材料、术语 | terminology_table（逐语种） |
| 3 逐语种检索 | 120–600s | queries | 每条命中 + 来源 + 逐字证据 |
| 4 语言/版本识别 | 300–720s | results | 每条 body_language/version + dedup 判定 |
| 5 对齐与误译复核 | 480–840s | 命中与原文 | evidence_map、conflicts、missing |
| 6 收尾自检 | 720–900s | 全部产物 | validate 通过 + change_log + 最终 status |

每步准则：
- 翻译与查询必须记录翻译服务与版本，保留原文-译文对照；未记录来源的译文不得当作已核对；
- 检索用 web_search / paper_search，逐语种分别构造查询（pt、en 至少各一组），不得只用单一语种；
- 每条命中必须有 locator（URL/DOI/材料段落定位）与逐字 quote，缺失标 `pending_human`；
- 语言与版本判定写依据（正文语言、摘要语言、双语刊声明、出版方翻译声明）；`abstract_only` 与 `publisher_translation` 必须区分，不得把“仅英语摘要”当“英语全译”；
- 冲突信息不隐藏：同一指标来源不一致时两个都记录并标 `source_conflict`。

**预算记账**：每次检索/翻译调用后，在 `work/state.json` 的 `steps` 里追加 `{step, at, requests_used, elapsed_s}`，并在 `change_log` 追加一条；超过 60 次请求或 850 秒即停止新增检索，进入收尾。禁止为凑语种数做无意义查询。

**确定性（本版重点）**：结果列表须按规范键排序（records 按 `work_key` + `title` 升序）；`scripts/main.py patch/finalize` 自动对 dict 键排序并重算 `determinism_fingerprint`（sha256，排除 `runtime`/`change_log` 等运行元数据）。同一输入重复运行，语义内容指纹必须一致；`runtime` 与时间戳属运行元数据，不影响可复核性。

## 5. 自检清单（对应评分维度，逐条勾选）
- [ ] `case_id` 已解析并写入；
- [ ] `output_contract` 全部字段已回填，缺值用 `null`/`[]`，未改名、未删键；
- [ ] `status` 诚实（ok/partial/insufficient_evidence/input_error），未编造数据；
- [ ] 研究问题、源语言、目标语种、检索范围、`known_item` 已明确；
- [ ] `terminology_table` 存在，每条含 type、basis、verification；
- [ ] 逐语种查询已记录，含翻译服务与版本、原文-译文对照；
- [ ] 每条结果有来源定位与逐字证据，关键非英语文献被覆盖；
- [ ] 每条命中标注 `body_language` 与 `version`，`abstract_only`/`publisher_translation`/`original` 区分正确；
- [ ] 同一论文多语版本已去重并记录依据（`dedup` + `work_key`）；
- [ ] `missing`/`conflicts`/权限与接口异常已如实说明；
- [ ] 术语表、查询式、语种判断依据可被第三方独立复核；
- [ ] `result.json` 是合法 UTF-8 JSON，`scripts/main.py validate` 无 issues；
- [ ] 骨架在 3 分钟内落盘；`scripts/selftest.py` 通过（含确定性）；
- [ ] 同一输入重复运行语义指纹一致（无随机性、无时间依赖的排序差异）。

## 6. 降级策略（不得编造）
- 缺 `case.json`/`task.md` → `status=input_error`，列出缺口，退出码 0；
- 坏 JSON/坏编码 → 记入 `errors`/`degradations`，继续处理其余材料；
- 网络或检索工具不可用 → `insufficient_evidence` 或 `partial`，明确已完成与未完成；
- 翻译服务失败/限额 → 回退纯术语表查询，未验证译文标 `unverified_translation`；
- 语种无法确定 → `body_language:"undetermined"`，写入 `uncertainty`；
- 原文不可得或仅摘要 → `version` 标 `abstract_only` 或 `undetermined`，不冒充全文；
- 材料缺失关键数值 → `not_reported` 或 `pending_human`，不补造；
- 任何失败都要留下合法的最小产物，不让进程崩溃；同一命令失败最多重试 2 次，之后降级。

## 7. 脚本命令
- `python3 -B scripts/main.py init --input /input --output /app/output --work /app/work`：发现输入、写骨架；
- `python3 -B scripts/main.py patch --patch p.json ...`：深合并补丁（列表整体替换、字典递归合并）并重算指纹；
- `python3 -B scripts/main.py finalize ...`：收尾重算指纹、记录 runtime；
- `python3 -B scripts/main.py validate --input /input --output /app/output`：校验契约字段与 JSON 合法性，输出缺失清单；
- `python3 -B scripts/main.py status --output /app/output`：进度摘要；
- `python3 -B scripts/selftest.py`：临时目录自检（正常/空目录/坏 JSON/坏编码/确定性/CLI）。

方法规则见 `references/method.md`，逐条自检见 `references/checklist.md`。脚本仅依赖 Python 标准库。

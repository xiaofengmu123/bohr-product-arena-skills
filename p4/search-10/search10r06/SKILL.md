---
name: crosslingual-literature-search
description: 跨语言科研文献检索 Skill。从研究问题出发确定源语言与目标语种，建立带溯源与验证方式的术语表，生成逐语种查询，取回并筛选文献，逐条判定语言与版本（原文/出版方全文译文/仅摘要/重复），对同一论文的多语版本去重，并把 plan.json 与 evidence.json 作为可复核的中间表示、result.json 作为其确定性投影。用于需要找回非英语原始研究、术语对齐可核验、版本可追溯的科研检索任务。
version: 1.1.0
---

# 跨语言文献检索

## 1. 角色与何时使用
本 Skill 面向“跨语言科研文献检索”：给定研究问题，找回相关文献（尤其非英语原始研究、本地期刊与区域会议论文），并说清每条命中的语言、版本与相关性依据。

适用：问题涉及多个语种、需要术语对齐（同义词/缩写/上下位/音译/本地译名）、需要区分原文/译文/双语刊/仅摘要并去重、需要结果可被第三方复核。

不适用：整篇翻译、写作润色、未经授权的跨境数据抓取、无证据要求的一次性问答。

判断是否做对不看接入多少接口或报告多少语种，只看三件事：非英语文献是否真实找回、术语对齐能否人工核对、每条命中能否说清来自哪个语种和哪个版本。

## 2. 输入契约
运行时原始输入固定位于 `/input/`：
- `/input/case.json`：案例任务、输入约束、可能的 `output_contract`、材料清单；
- `/input/task.md`：题目说明与评分维度；
- `/input/materials/`（若存在）：科研材料、PDF 原文/译文等事实依据。

发现而非假设：不假设固定文件名/键名，用 `init` 自动发现；`output_contract`（或 `outputContract`/`output_contract_fields`）可能藏在 case.json 深层或 task.md 内联 JSON 里，都要解析。文本按 UTF-8 容错读取（BOM、坏编码降级，不崩溃）；PDF 用平台 pdftotext 解析实际文件，不依据文件名或标签假定内容，必要时 OCR 兜底；单文件超 2MB 跳过并记入 `degradations`。工作目录 `/app/work/`，最终产物写 `/app/output/`。

## 3. 中间表示与可复核（本版核心设计）
不把结论直接写进 result.json，而是先落到两个结构化中间产物，result.json 只是它们的确定性投影：

- `/app/work/plan.json`：`research_question`、`source_language`、`target_languages`、`scope`、`known_item_hint`、`terminology_table`（每条含 term/language/type/basis/verification）、`queries`（逐语种，含翻译服务与版本、原文-译文对照）。
- `/app/work/evidence.json`：`hits`（每条含 title/authors/year/venue/doi/url/language/version/body_language/attribution/locator/quote/relevance）、`evidence_map`、`dedup`、`missing`、`conflicts`。

`assemble` 从 plan.json + evidence.json 重建 result.json，并写入两者的 SHA-256（`intermediate.plan_sha256` / `intermediate.evidence_sha256`）。评审者可对中间产物重算哈希，确认 result.json 未被篡改、逐条结论都能指回 locator + 逐字 quote。同一输入重复运行结果一致（无随机性、change_log 用单调 seq 不用墙钟排序）。

## 4. 输出契约
必须产出 `/app/output/result.json`，包含：
- `case_id`（从输入解析；无法解析时 `status=input_error` 并说明）；
- `output_contract` 的全部字段逐一回填，缺值给 `null`/`[]`，不改名、不删未知键；
- 附加标准字段：`status`、`sources`、`uncertainty`、`change_log`、`errors`、`degradations`、`pending_human`、`runtime`、`intermediate`。

`status` 取值：`ok`（完整完成）、`partial`（部分完成并说明）、`insufficient_evidence`（材料不足）、`input_error`（输入缺失/损坏）。

默认主结果字段（若 case 提供 contract 以 contract 为准，并补齐下列语义字段）：`research_question`、`terminology_table`、`queries`、`results`、`evidence_map`、`dedup`、`missing`、`conflicts`。

每条命中必须带语言与版本判定：`version ∈ {original, translation, abstract_only, duplicate, undetermined}`、`body_language`（正文语言）、`attribution`（出版方/译者/期刊声明），并保留 `locator` + 逐字 `quote`。可编辑副产物（`terminology_table.md`、`queries.md` 等）与 result.json 一并放 `/app/output/`。

## 5. 执行流程（总预算 900 秒、64 次模型请求）
第 0 步必须最先执行并立即落盘骨架，**必须在第 3 分钟（180 秒）内完成**：

```bash
python3 -B scripts/main.py init --input /input --output /app/output --work /app/work
```

该命令发现输入、解析 `case_id` 与契约，原子写入合法 result.json 骨架（全部契约字段、`status:"partial"` 初值）以及 plan.json/evidence.json 中间产物。之后任何异常都不会留下不完整产物。

之后按步骤增量改进，每步把新证据写进中间产物并重投影（也可用 `patch` 直接改标准字段）：

```bash
python3 -B scripts/main.py plan     --plan <计划补丁>.json   --work /app/work --output /app/output
python3 -B scripts/main.py evidence --evidence <证据补丁>.json --work /app/work --output /app/output
python3 -B scripts/main.py patch    --patch <补丁>.json      --output /app/output --work /app/work
```

| 步骤 | 时间窗 | 输入 | 输出 |
|---|---|---|---|
| 0 初始化 | 0–60s | /input 目录 | result.json 骨架 + plan.json/evidence.json 骨架 + state.json |
| 1 读题定范围 | 0–180s | case.json、task.md | research_question、源/目标语种、known_item_hint |
| 2 术语对齐 | 60–300s | 研究问题、材料、术语 | terminology_table（逐语种，带 basis/verification） |
| 3 逐语种检索 | 120–600s | queries | 每条命中 + 来源 + 逐字证据 |
| 4 语言/版本识别 | 300–720s | results | 每条 language/version/body_language + dedup 判定 |
| 5 对齐与误译复核 | 480–840s | 命中与原文 | evidence_map、conflicts、missing |
| 6 收尾自检 | 720–900s | 全部产物 | validate 通过 + change_log + 最终 status |

每步准则：
- 翻译与查询必须记录翻译服务与版本、保留原文-译文对照；未记录来源的译文不得当作已核对；
- 逐语种分别构造查询，不得只用母语；检索用 web_search / paper_search；
- 每条命中必须有 locator（URL/DOI/材料段落定位）与逐字 quote，缺失标 `pending_human`；
- 语言与版本判定写依据（正文语言、摘要语言、双语刊声明、出版方翻译声明）；原文不可得标 `abstract_only`/`undetermined`，不冒充全文；
- 冲突不隐藏：同一指标来源不一致时两者都记录并标 `source_conflict`。

## 6. 自检清单（对应评分维度，逐条勾选）
- [ ] `case_id` 已解析并写入；
- [ ] `output_contract` 全部字段已回填，缺值用 `null`/`[]`，未改名、未删键；
- [ ] `status` 诚实（ok/partial/insufficient_evidence/input_error），未编造数据；
- [ ] 研究问题、源语言、目标语种、检索范围已明确；
- [ ] `terminology_table` 存在，每条含 term/language/type/basis/verification；
- [ ] 逐语种查询已记录，含翻译服务与版本、原文-译文对照；
- [ ] 每条结果有 locator 与逐字 quote，关键非英语文献被覆盖；
- [ ] 每条命中标注 language/version/body_language/attribution，同论文多语版本已去重并记录依据；
- [ ] `missing`/`conflicts`/权限与接口异常已如实说明；
- [ ] 术语表、查询式、语种判断依据可被第三方独立复核；
- [ ] plan.json 与 evidence.json 已落盘，result.json 含两者 SHA-256；
- [ ] `scripts/main.py validate` 无 issues；骨架在 3 分钟内落盘；重复运行结果一致。

## 7. 降级策略（不得编造）
- 缺 `case.json`/`task.md` → `status=input_error`，列出缺口，退出码 0；
- 坏 JSON/坏编码 → 记入 `errors`/`degradations`，继续处理其余材料；
- 网络或检索工具不可用 → `insufficient_evidence` 或 `partial`，说明已完成与未完成；
- 翻译服务失败/限额 → 回退纯术语表查询，未验证译文标 `unverified_translation`；
- 语种无法确定 → `language:"undetermined"`，写入 `uncertainty`；
- 原文不可得或仅摘要 → 版本标 `abstract_only`/`undetermined`，不冒充全文；
- 材料缺失关键数值 → `not_reported` 或 `pending_human`，不补造；
- 任何失败都留下合法最小产物；同一命令失败最多重试 2 次，之后降级。

## 8. 脚本命令
脚本仅依赖 Python 标准库，路径相对 Skill 根目录（先 `cd /skills/weekly-skill` 再执行）：
- `python3 -B scripts/main.py init ...`：发现输入、写骨架与中间产物；
- `python3 -B scripts/main.py plan --plan p.json`：并入 plan.json 并重投影 result.json；
- `python3 -B scripts/main.py evidence --evidence e.json`：并入 evidence.json 并重投影；
- `python3 -B scripts/main.py patch --patch p.json`：深合并标准字段到 result.json；
- `python3 -B scripts/main.py assemble`：从中间产物确定性重建 result.json；
- `python3 -B scripts/main.py validate`：校验契约字段与 JSON 合法性，输出缺失清单；
- `python3 -B scripts/main.py status`：摘要当前进度与 change_log；
- `python3 -B scripts/selftest.py`：本地自检（临时目录，不触碰 /input、/app/output）。

方法规则见 `references/method.md`，逐条自检见 `references/checklist.md`。

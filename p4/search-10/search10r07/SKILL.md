---
name: crosslingual-literature-search
description: 跨语言科研文献检索 Skill。从研究问题出发确定源语言与目标语种，建立可复核术语表，生成逐语种查询，取回并筛选文献，判定每条命中的语言与版本（原文/译文/双语刊/仅摘要），对同一论文的多语版本去重，并为每条结果保留来源定位与逐字证据。用于需要找回非英语原始研究、术语对齐可核验、版本可追溯的科研检索任务。
version: 1.1.0-robust
---

# 跨语言科研文献检索

## 1. 角色与何时使用

本 Skill 面向“跨语言科研文献检索”：给定研究问题，找回相关文献，尤其是被母语查询系统性漏掉的非英语原始研究、本地期刊与区域会议论文，并说明每条命中的语言、版本与相关性依据。

适用：研究问题涉及多语种；需要术语对齐（同义词、缩写、上下位、音译、本地译名）且可复核；需要区分原文/译文/双语刊/仅摘要并去重同一论文的多语版本。

不适用：整篇翻译、写作润色、未经授权的跨境数据抓取、无证据要求的一次性问答。

## 2. 输入契约

运行时原始输入固定位于 `/input/`，发现而不假设：

- `/input/case.json`：案例任务、输入约束、可能的 `output_contract`、材料清单；
- `/input/task.md`：题目总说明与评分维度；
- `/input/materials/`（若存在）：PDF 原文/译文等事实依据。

规则：不假设固定文件名或键名，用 `scripts/main.py init` 自动发现；`output_contract` 可能出现在 `case.json` 或 `task.md` 内嵌 JSON 中，全部字段必须原样回填；文本按 UTF-8 容错读取（BOM、坏编码降级，不崩溃）；单文件超 2MB 跳过并记入 `degradations`；PDF 用平台 pdftotext 解析实际文件，不依据文件名或标签假定内容。工作目录 `/app/work/`，产物写 `/app/output/`。

## 3. 输出契约

必须产出 `/app/output/result.json`，包含：

- `case_id`（从输入解析；无法解析时 `status=input_error` 并说明）；
- `output_contract` 的全部字段逐一回填，缺值给 `null`/`[]`，不改名、不删未知键；
- 附加标准字段：`status`、`sources`、`uncertainty`、`pending_human`、`change_log`，以及 `errors`、`degradations`、`runtime`。

`status` 取值：`ok`（完整完成）、`partial`（部分完成并说明已完成与未完成）、`insufficient_evidence`（材料不足）、`input_error`（输入缺失或损坏）。

默认主结果字段（case 提供 contract 时以 contract 为准，并补齐下列语义字段）：`research_question`、`terminology_table`、`queries`、`results`、`evidence_map`、`dedup`、`missing`、`conflicts`。

每条结果的证据溯源字段（硬要求）：`locator`（URL/DOI/材料段落定位）+ `quote`（逐字引用）+ `language` + `version` + `relevance_basis`。可编辑副产物（如 `terminology_table.md`、`queries.md`）与 `result.json` 一并放 `/app/output/`。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

**第 0 步必须先执行，立即落盘结果骨架（前 3 分钟内）：**

```bash
python3 -B scripts/main.py init --input /input --output /app/output --work /app/work
```

该命令发现输入、解析 `case_id` 与契约，原子写入合法 `result.json` 骨架（全部契约字段、`status:"partial"` 初值、空结果列表）。任何后续异常都不会留下不完整产物。**必须在第 3 分钟（180 秒）内完成。**

之后按步骤增量改进，每步把新证据 patch 回 `result.json`：

```bash
python3 -B scripts/main.py patch --output /app/output --work /app/work --patch <补丁文件>.json
```

| 步骤 | 时间窗 | 输入 | 输出 |
|---|---|---|---|
| 0 初始化 | 0–60s | /input 目录 | result.json 骨架 + work/state.json |
| 1 读题定范围 | 0–180s | case.json、task.md | research_question、源/目标语种、范围 |
| 2 术语对齐 | 60–300s | 研究问题、材料、术语 | terminology_table（逐语种） |
| 3 逐语种检索 | 120–600s | queries | 每条命中 + 来源 + 逐字证据 |
| 4 语言/版本识别 | 300–720s | results | 每条 language/version + dedup 判定 |
| 5 对齐与误译复核 | 480–840s | 命中与原文 | evidence_map、conflicts、missing |
| 6 收尾自检 | 720–900s | 全部产物 | validate 通过 + change_log + 最终 status |

每步准则：翻译与查询必须记录所用翻译服务与版本并保留原文-译文对照，未记录来源的译文不得当作已核对；检索用 web_search / paper_search，逐语种分别构造查询，不得只用母语；每条命中必须有 locator 与逐字 quote，缺失标 `pending_human`；语言与版本判定写依据（正文语言、摘要语言、双语刊声明、出版方翻译声明）；冲突信息不隐藏，同指标来源不一致时两个都记录并标 `source_conflict`。

## 5. 脚本命令

- `python3 -B scripts/main.py init [--input --output --work]`：发现输入、写骨架；
- `python3 -B scripts/main.py patch --patch p.json`：深合并补丁（字典递归合并、列表整体替换）；补丁顶层 `_note` 会追加为 change_log 条目；
- `python3 -B scripts/main.py validate`：校验契约字段与 JSON 合法性，输出缺失清单（有 issues 退出码 1）；
- `python3 -B scripts/main.py status`：摘要当前进度与 change_log；
- `python3 -B scripts/main.py selftest` / `python3 -B scripts/selftest.py`：鲁棒性自检。

方法规则见 `references/method.md`，逐条自检见 `references/checklist.md`。脚本仅依赖 Python 标准库；`scripts/core.py` 提供脚本识别辅助函数 `detect_script`（脚本≠语种，语种须由出版方/正文证据确认）。

## 6. 自检清单（逐条勾选）

- [ ] `case_id` 已解析并写入；
- [ ] `output_contract` 全部字段已回填，缺值用 `null`/`[]`，未改名、未删键；
- [ ] `status` 诚实（ok/partial/insufficient_evidence/input_error），未编造数据；
- [ ] 研究问题、源语言、目标语种、检索范围已明确；
- [ ] `terminology_table` 存在，每条含 type、basis、verification；
- [ ] 逐语种查询已记录，含翻译服务与版本、原文-译文对照；
- [ ] 每条结果有来源定位与逐字证据，关键非英语文献被覆盖；
- [ ] 每条命中标注语言与版本，同论文多语版本已去重并记录依据；
- [ ] `missing`/`conflicts`/权限与接口异常已如实说明；
- [ ] 术语表、查询式、语种判断依据可被第三方独立复核；
- [ ] `result.json` 是合法 UTF-8 JSON，`validate` 无 issues；
- [ ] 骨架在 3 分钟内落盘；同一输入重复运行结果一致（仅 runtime 计时允许不同）。

## 7. 降级策略（不得编造）

| 场景 | 处置 |
|---|---|
| 缺 `case.json`/`task.md` | `status=input_error`，列出缺口，退出码 0 |
| 坏 JSON / 坏编码 | 记入 `errors`/`degradations`，继续处理其余材料 |
| 网络或检索工具不可用 | `insufficient_evidence` 或 `partial`，明确已完成与未完成 |
| 翻译服务失败/限额 | 回退纯术语表查询，未验证译文标 `unverified_translation` |
| 语种无法确定 | `language:"undetermined"`，写入 `uncertainty` |
| 原文不可得或仅摘要 | 版本标 `abstract_only` 或 `undetermined`，不冒充全文 |
| 材料缺失关键数值 | `not_reported` 或 `pending_human`，不补造 |
| 超大文件（>2MB） | 跳过并记入 `degradations`，不影响其余处理 |

任何失败都留下合法的最小产物，不让进程崩溃；同一命令失败最多重试 2 次，之后降级。脚本入口对空输入、坏 JSON、缺参数、超大文件均优雅降级，退出码保持 0（除 `validate` 与 `selftest` 按结果返回）。

## 8. 本版设计取舍（鲁棒性优先）

本版与“完整功能版”的关键差异：**不把鲁棒性当作锦上添花，而是作为默认路径**。具体表现为：

- 所有 I/O 走容错函数，坏编码/坏 JSON 只降级不抛异常；
- 骨架先落盘，中途任何异常都不会留下半截 JSON（原子写：临时文件 + `os.replace`）；
- 列表排序确定（输入顺序/排序键），无随机性，可重复运行比对；
- 语种识别只做“脚本级”提示并显式标注 `script_only`，不把脚本误当语种；
- 补丁深合并把“字典递归、列表整体替换”写成显式规则，避免增量更新丢字段。

## 9. 最小调用示例

输入（示意，非正式案例）：

```json
{"case_id":"demo","output_contract":{"research_question":"","target_languages":[],"results":[]}}
```

调用（`--input/--output/--work` 省略时用默认路径）：

```bash
python3 -B scripts/main.py init
python3 -B scripts/main.py patch --patch step1.json
python3 -B scripts/main.py validate
```

示意输出（标为示例，非正式结果；results 条目 schema）：

```json
{"case_id":"demo","status":"partial",
 "results":[{"title":"示例标题","authors":["示例作者"],"year":2024,
   "venue":"示例期刊","doi_or_url":"https://example.org/10.0000/example",
   "language":"pt","version":"original","source":"web_search",
   "locator":"https://example.org/paper","quote":"逐字引文示例",
   "relevance_basis":"示例相关性依据"}],
 "sources":[],"uncertainty":[],"pending_human":[],"change_log":[],
 "errors":[],"degradations":[],"runtime":{"seconds_elapsed":0}}
```

说明：以上标题/作者/DOI 均为占位符，仅示意字段结构，不冒充正式案例结果。

## 10. 边界

不接通用大而全 Skill：本 Skill 只解决跨语言文献检索一类任务。没有真实材料时输出“材料不足”，不以示例填充。术语表、查询式、语种与版本判定必须可被第三方独立复核；不虚构引用、DOI、数值。

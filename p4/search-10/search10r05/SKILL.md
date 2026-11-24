---
name: crosslingual-literature-search
description: 跨语言科研文献检索 Skill，面向“已知条目”检索：从研究问题确定源语言与目标语种，建立可复核术语表，生成逐语种查询，取回并筛选文献，判定每条命中的语言与版本（原文/出版社完整译文/仅摘要/双语刊），对同一论文多语版本去重，并为每条结果保留来源定位与逐字证据。用于需要找回非英语原始研究、术语对齐可核验、版本可追溯、输出契约字段逐一对齐的科研检索任务。
version: 2.0.0
---

# 跨语言文献检索（已知条目版）

## 1. 角色与何时使用

本 Skill 完成一类明确任务：给定研究问题，找回相关文献（尤其非英语原始研究、
本地期刊、区域会议论文），并说清每条命中的语种、版本与相关性依据。**重点场景
是“已知条目”检索**——问题已指向可唯一识别的作品，需要找回并还原其
原文/译文/仅摘要多语种视图，而不是开放式泛搜。

适用：需要术语对齐（同义词、缩写、上下位、音译、本地译名）且结果可人工复核；
需要区分原文、出版社完整译文、仅英文摘要、双语刊并对多语版本去重；需要
`result.json` 与 `output_contract` 字段逐一对齐。

不适用：整篇翻译、写作润色、未经授权的跨境数据抓取、无证据要求的一次性问答。

## 2. 输入契约（先发现，再假设）

运行时原始输入固定位于 `/input/`：
- `/input/case.json`：案例任务、输入约束、可能的 `output_contract`、材料清单；
- `/input/task.md`：题目总说明与评分维度（方法与自检对齐依据）；
- `/input/materials/`（若存在）：科研材料、PDF 原文/译文等事实依据。

规则：
- 不假设固定文件名/键名，用 `scripts/main.py init` 自动发现；
- `output_contract`（或 `outputContract`）的全部字段必须原样回填，缺值给
  `null`/`[]`/`{}`，不改名、不删未知键；
- 文本统一按 UTF-8 容错读取（BOM、坏编码降级，不崩溃）；单文件超 2MB 跳过并记
  `degradations`；
- PDF 用平台 pdftotext 解析实际文件，正文语种来自实际文本，不依据文件名或标签；
- 工作目录 `/app/work/`，最终产物写 `/app/output/`。

## 3. 输出契约

必须产出 `/app/output/result.json`，其顶层字段与 `output_contract` **逐一对齐**：

1. `case_id`：从输入解析；无法解析时 `status=input_error` 并说明；
2. `output_contract` 的全部字段：逐一回填，缺值 `null`/`[]`/`{}`，不改名、不删未知键；
3. 标准元字段（契约之外恒有）：`status`、`sources`、`uncertainty`、`change_log`、
   `errors`、`degradations`、`pending_human`、`runtime`、`contract_schema`；
4. `contract_schema` 记录契约字段名、类型与回填默认值，供第三方核验对齐关系。

`status` 取值：`ok`（完整完成）、`partial`（部分完成，如实说明已完成与未完成）、
`insufficient_evidence`（材料不足）、`input_error`（输入缺失或损坏）。

契约缺失时使用默认语义字段：`research_question`、`terminology_table`、`queries`、
`results`、`evidence_map`、`dedup`、`missing`、`conflicts`，并同样补齐标准元字段。

`change_log` 记录每一次落盘变更：`seq`（从 0 递增）、`action`（init/patch）、
`fields`（本次触及字段）、`status`、可选 `note`；init 即写入第 0 条，此后只增量。

可编辑副产物（如 `terminology_table.md`、`queries.md`）与 `result.json` 一并放
`/app/output/`。

## 4. 执行流程（总预算 900 秒、64 次模型请求，单响应含推理 ≤16384 token）

第 0 步必须最先执行，立即落盘结果骨架：

```bash
python3 -B scripts/main.py init --input /input --output /app/output --work /app/work
```

该命令发现输入、解析 `case_id` 与契约，原子写入合法 `result.json` 骨架（全部契约
字段、`status:"partial"`、空结果列表、`change_log` 第 0 条）。任何后续异常都不会
留下不完整产物。**必须在第 3 分钟（180 秒）内完成。** 之后只做增量改进：

```bash
python3 -B scripts/main.py patch --output /app/output --work /app/work --patch <补丁>.json
```

| 步骤 | 时间窗 | 输入 | 输出 |
|---|---|---|---|
| 0 初始化 | 0–60s | /input 目录 | result.json 骨架 + work/state.json |
| 1 读题定范围 | 0–180s | case.json、task.md | research_question、源/目标语种、范围 |
| 2 术语对齐 | 60–300s | 研究问题、材料 | terminology_table（逐语种） |
| 3 逐语种检索 | 120–600s | queries | 每条命中 + locator + 逐字证据 |
| 4 语言/版本识别 | 300–720s | results | 每条 language/body_language/version + dedup |
| 5 误译/回译复核 | 480–840s | 命中与原文 | evidence_map、conflicts、missing |
| 6 收尾自检 | 720–900s | 全部产物 | validate 通过 + change_log + 最终 status |

每步准则：
- 翻译与查询必须记录翻译服务与版本，保留原文—译文对照；未记录来源的译文标
  `unverified_translation`；
- 检索用 web_search / paper_search，逐语种构造查询，不得只用母语；
- 每条命中必须有 locator（URL/DOI/材料段落）与逐字 quote，缺失标 `pending_human`；
- 语言与版本判定写依据（正文语言、摘要语言、双语刊声明、出版方翻译声明）；
- 冲突信息不隐藏：同一指标来源不一致时两个都记录并标 `source_conflict`。

## 5. 自检清单（对应评分维度，逐条勾选）

- [ ] `case_id` 已解析并写入；
- [ ] `output_contract` 全部字段已回填，缺值用 `null`/`[]`，未改名、未删键；
- [ ] `status` 诚实（ok/partial/insufficient_evidence/input_error），未编造数据；
- [ ] 研究问题、源语言、目标语种、检索范围已明确；
- [ ] `terminology_table` 存在，每条含 type、basis、verification；
- [ ] 逐语种查询已记录，含翻译服务与版本、原文-译文对照；
- [ ] 每条结果有来源定位与逐字证据，关键非英语文献被覆盖；
- [ ] 每条命中标注 language/body_language/version，多语版本去重并记录依据；
- [ ] `missing`/`conflicts`/权限与接口异常已如实说明；
- [ ] `change_log` 记录全部变更，`contract_schema` 可核验字段对齐；
- [ ] `result.json` 是合法 UTF-8 JSON，`scripts/main.py validate` 无 issues；
- [ ] `scripts/selftest.py` 全部 PASS；骨架在 3 分钟内落盘；重复运行结果一致。

## 6. 降级策略（不得编造）

- 缺 `case.json`/`task.md` → `status=input_error`，列出缺口，退出码 0；
- 坏 JSON/坏编码 → 记入 `errors`/`degradations`，继续处理其余材料；
- 网络或检索工具不可用 → `insufficient_evidence` 或 `partial`，说明已完成与未完成；
- 翻译服务失败/限额 → 回退纯术语表查询，未验证译文标 `unverified_translation`；
- 语种无法确定 → `language:"undetermined"`，写入 `uncertainty`；
- 原文不可得或仅摘要 → 版本标 `abstract_only` 或 `undetermined`，不冒充全文；
- 材料缺失关键数值 → `not_reported` 或 `pending_human`，不补造；
- 任何失败都留下合法最小产物，不崩溃；同一命令失败最多重试 2 次，之后降级。

## 7. 脚本命令

- `python3 -B scripts/main.py init`：发现输入、写骨架；
- `python3 -B scripts/main.py patch --patch p.json`：深合并补丁（列表整体替换、
  字典递归合并）并追加 change_log；
- `python3 -B scripts/main.py validate`：校验契约字段与 JSON 合法性，输出缺失清单；
- `python3 -B scripts/main.py status`：摘要当前进度与 change_log；
- `python3 -B scripts/selftest.py`：离线自检（合成输入，不写正式案例）。

方法规则见 `references/method.md`，逐条自检见 `references/checklist.md`。脚本仅
依赖 Python 标准库。

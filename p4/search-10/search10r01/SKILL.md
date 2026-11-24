---
name: crosslingual-literature-search
description: 跨语言科研文献检索 Skill。从研究问题确定源语言与目标语种，建立可复核的术语对齐表，生成逐语种查询，取回并筛选文献，判定每条命中的语言与版本（原文/译文/双语刊/仅摘要），对同一论文的多语版本去重，并为每条结论输出 locator + 逐字 quote 的细粒度 evidence_map。用于需要找回非英语原始研究、术语对齐可核验、版本可追溯、证据可独立复核的科研检索任务。
version: 1.1.0
---

# 跨语言文献检索（证据溯源强化版）

## 1. 角色与何时使用

本 Skill 面向“跨语言科研文献检索”任务：给定一个研究问题，找到相关文献，尤其找回非英语原始研究、本地期刊与区域会议论文，并说明每条命中的语言、版本与相关性依据。

适用场景：研究问题涉及多个语种或要求检索非英语文献；术语对齐（同义词、缩写、上下位、音译、本地译名）需可复核；需区分原文、译文、双语刊与仅摘要版本并对多语版本去重。

不适用：整篇翻译、写作润色、未经授权的跨境数据抓取、无证据要求的一次性问答。

核心判据只有三项：**非英语文献是否真实找回、术语对齐是否经得起人工核对、每条命中能否说清其语种与版本**。不比较接入接口数量或语种数量。

## 2. 输入契约

运行时原始输入固定位于 `/input/`：

- `/input/case.json`：案例任务、输入约束、可能的 `output_contract`、材料清单；
- `/input/task.md`：题目总说明与评分维度；
- `/input/materials/`（若存在）：科研材料、PDF 原文/译文等事实依据。

发现而非假设：

- 不假设固定文件名或键名，用 `python3 -B scripts/main.py init` 自动发现；
- `case.json` 若含 `output_contract`（或 `outputContract`），其全部字段必须原样回填，缺值给 `null`/`[]`，不改名、不删未知键；
- 文本统一按 UTF-8 容错读取（BOM、坏编码降级，不崩溃）；
- PDF 用平台 pdftotext 解析实际文件，不依据文件名或标签假定内容，必要时 OCR 兜底；
- 单文件超 2MB 时跳过并记入 `degradations`。

工作目录 `/app/work/`，最终产物写 `/app/output/`。

## 3. 输出契约

必须产出 `/app/output/result.json`，包含：

- `case_id`（从输入解析；无法解析时 `status=input_error` 并说明）；
- `output_contract` 的全部字段，逐一回填，缺值给 `null`/`[]`，不改名、不删未知键；
- 附加标准字段：`status`、`research_question`、`terminology_table`、`queries`、`results`、`evidence_map`、`dedup`、`sources`、`uncertainty`、`conflicts`、`missing`、`pending_human`、`errors`、`degradations`、`change_log`、`runtime`。

`status` 取值：`ok`（完整完成）、`partial`（部分完成，如实说明已完成与未完成）、`insufficient_evidence`（材料不足）、`input_error`（输入缺失或损坏）。

### 3.1 evidence_map（本版强化点，逐条结论必须可复核）

`evidence_map` 是一个列表，**每条关键结论/数值/命中/语言与版本判定都必须对应至少一条**。每条结构如下：

```json
{
  "evidence_id": "ev-001",
  "claim": "规范化结论文本",
  "claim_type": "terminology|query|language|version|dedup|relevance|metadata|conflict",
  "target": {"results_index": 0, "field": "language"},
  "locator": {"source": "material|web|paper_index|publisher_page",
              "path": "materials/a.pdf#p3 或 URL 或 DOI",
              "offset": "段落/页/行定位"},
  "quote": "逐字原文（UTF-8）",
  "quote_language": "pt|en|zh|ja|...",
  "quote_hash": "sha1 前 12 位",
  "source_meta": {"doi": "...", "url": "...", "retrieved_at": "..."},
  "basis": "采信理由（正文语言、双语刊声明、出版方翻译声明）",
  "verification": "第三方可独立复核的方式",
  "confidence": "high|medium|low",
  "conflict": null,
  "status": "verified|unverified|pending_human"
}
```

字段要求：

- `claim` 一句话说清结论；`claim_type` 限定结论类别；
- `locator` 必须含 `source` 与 `path`，`offset` 给出可定位细节；
- `quote` 必须是从 `locator` 指到位置复制出的**逐字文本**，不得改写；`quote_hash` 用 `scripts/main.py` 的 `hash_text` 计算，供复核；
- `target` 可选，把证据链到 `results` 中的具体条目；结果一旦带 `language`/`version`/`relevance` 字段，就必须有指向它的证据，否则 `validate` 报错；
- `status=unverified` 或 `pending_human` 时，`verification` 必须写清还缺什么、由谁补齐。

### 3.2 其余主结果字段

- `research_question`：澄清后的问题、源语言、目标语种、检索范围；
- `terminology_table`：术语对齐表（源词、语种、变体、类型、依据、验证方式）；
- `queries`：逐语种查询式，含翻译服务与版本、原文-译文对照；
- `results`：候选文献（标题、作者、年份、期刊/会议、DOI/URL、语言、版本、来源、相关性依据）；
- `dedup`：同论文多语版本去重判定与依据，含 `canonical`、`duplicate_of`、`basis`；
- `missing` / `conflicts`：遗漏、无结果、冲突、权限问题，逐条说明。

可编辑副产物（如 `terminology_table.md`、`queries.md`）与 `result.json` 一并放 `/app/output/`。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

**第 0 步必须最先执行，并在第 3 分钟（180 秒）内完成**，立即落盘结果骨架：

```bash
python3 -B scripts/main.py init --input /input --output /app/output --work /app/work
```

该命令发现输入、解析 `case_id` 与契约，原子写入合法 `result.json` 骨架（全部契约字段、`status:"partial"` 初值、空结果列表）。任何后续异常都不会留下不完整产物。

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

每步准则：

- 翻译与查询必须记录所用翻译服务与版本，保留原文-译文对照；未记录来源的译文不得当作已核对；
- 检索用 web_search / paper_search，逐语种分别构造查询，不得只用母语；
- 每条命中必须有 locator（URL/DOI/材料段落定位）与逐字 quote，缺失标 `pending_human`；
- 语言与版本判定写依据（正文语言、摘要语言、双语刊声明、出版方翻译声明）；
- 冲突信息不隐藏：同一指标来源不一致时两个都记录并标 `source_conflict`。

### 4.1 去重与版本判定（已知边界：葡/英双向、译文/仅摘要/重复）

- 先按规范化 DOI 归并，再按规范化标题（去变音、去标点、小写）归并；同组内标记 `canonical` 与 `duplicate_of`；
- “仅英文摘要”不得冒充英文全文，版本标 `abstract_only`；出版方全文翻译标 `translation`，并写明翻译方向（pt→en / en→pt）；
- 同一论文以原文+译文出现时只计一次，`dedup` 记录依据与双方 locator。

## 5. 自检清单（对应评分维度，逐条勾选）

- [ ] `case_id` 已解析并写入；
- [ ] `output_contract` 全部字段已回填，缺值用 `null`/`[]`，未改名、未删键；
- [ ] `status` 诚实（ok/partial/insufficient_evidence/input_error），未编造数据；
- [ ] 研究问题、源语言、目标语种、检索范围已明确；
- [ ] `terminology_table` 存在，每条含 type、basis、verification；
- [ ] 逐语种查询已记录，含翻译服务与版本、原文-译文对照；
- [ ] 每条结果有来源定位与逐字证据，关键非英语文献被覆盖；
- [ ] 每条命中标注语言与版本，同论文多语版本已去重并记录依据；
- [ ] `evidence_map` 每条含 locator + 逐字 quote + quote_hash + status，链到对应结果；
- [ ] `missing`/`conflicts`/权限与接口异常已如实说明；
- [ ] 术语表、查询式、语种判断依据可被第三方独立复核；
- [ ] `result.json` 是合法 UTF-8 JSON，`python3 -B scripts/main.py validate` 无 issues；
- [ ] 骨架在 3 分钟内落盘；同一输入重复运行结果一致（无随机性）；
- [ ] `python3 -B scripts/selftest.py` 全部通过。

## 6. 降级策略（不得编造）

- 缺 `case.json`/`task.md` → `status=input_error`，列出缺口，退出码 0；
- 坏 JSON/坏编码 → 记入 `errors`/`degradations`，继续处理其余材料；
- 网络或检索工具不可用 → `insufficient_evidence` 或 `partial`，明确已完成与未完成；
- 翻译服务失败/限额 → 回退纯术语表查询，未验证译文标 `unverified_translation`；
- 语种无法确定 → `language:"undetermined"`，写入 `uncertainty`；
- 原文不可得或仅摘要 → 版本标 `abstract_only` 或 `undetermined`，不冒充全文；
- 材料缺失关键数值 → `not_reported` 或 `pending_human`，不补造；
- 证据无逐字 quote 或 locator → 该条标 `pending_human`，不得进入 `status=ok`；
- 任何失败都要留下合法的最小产物，不让进程崩溃；同一命令失败最多重试 2 次，之后降级。

## 7. 脚本命令

- `python3 -B scripts/main.py init ...`：发现输入、写骨架；
- `python3 -B scripts/main.py patch --patch p.json`：深合并补丁（列表整体替换、字典递归合并）；
- `python3 -B scripts/main.py validate`：校验契约字段、JSON 合法性与 evidence_map，输出缺失清单；
- `python3 -B scripts/main.py status`：摘要当前进度与 change_log；
- `python3 -B scripts/selftest.py`：离线自检（空输入、坏 JSON、契约保留、证据校验、去重/语种 helper）。

方法规则见 `references/method.md`，逐条自检见 `references/checklist.md`。脚本仅依赖 Python 标准库。

---
name: crosslingual-literature-search
description: 跨语言科研文献检索 Skill。从研究问题或已知文献线索出发，确定源语言与目标语种，建立可复核的术语表，生成逐语种查询，取回并筛选文献，判定每条命中的语言与版本（原文/译文/双语刊/仅摘要），对同一论文的多语版本去重，并为每条结果保留来源定位与逐字证据。适用于需要找回非英语原始研究、术语对齐可核验、版本可追溯的科研检索任务，支持"已知文献核验"与"主题发现"两种模式。
version: 2.0.0
---

# 跨语言文献检索

## 1. 角色与何时使用
本 Skill 把"跨语言科研文献检索"沉淀为可复核、可复现、可增量改进的流程：从研究问题（或给定的已知文献线索）出发，明确源语言与目标语种，建立带依据与验证方式的术语表，生成逐语种查询式，取回并筛选候选文献，判定每条命中的语言与版本，对同一论文的多语版本去重，并为每条关键结论保留来源定位与逐字证据。

适用场景：
- 研究问题涉及多语种，或明确要求找回非英语文献、本地期刊与区域会议论文；
- 已知一篇文献的题录/DOI/线索，需要在另一语种核验其原文、译文、摘要版本并去重（known_item 模式）；
- 需要术语对齐（同义词、缩写、上下位、音译、本地译名）且结果可被人工复核；
- 需要区分原文、译文、双语刊、仅摘要版本，并对同一论文的多语版本去重。

不适用：整篇翻译、写作润色、未经授权的跨境数据抓取、无证据要求的一次性问答。

## 2. 输入契约
运行时原始输入固定位于 `/input/`：
- `/input/case.json`：案例任务、输入约束、可能的 `output_contract`、材料清单；
- `/input/task.md`：题目总说明与评分维度（方法与自检对齐依据）；
- `/input/materials/`（若存在）：科研材料、PDF 原文/译文等事实依据。

发现而非假设：
- 不假设固定文件名或键名；先运行 `scripts/main.py init` 自动发现；
- `case.json` 若含 `output_contract`/`outputContract`，其全部顶层字段必须原样回填到 `result.json`，缺值给 `null`/`[]`，不改名、不删未知键；
- 文本按 UTF-8 容错读取（BOM、坏编码降级，不崩溃），单文件超 2MB 跳过并记入 `degradations`；
- PDF 用平台 pdftotext 解析实际文件，不依据文件名或标签假定内容；语言与版本以正文实际文字判定。

工作目录 `/app/work/`，最终产物写 `/app/output/`。

## 3. 输出契约
必须产出 `/app/output/result.json`，顶层包含：
- `case_id`（从输入解析；无法解析时 `status=input_error` 并说明）；
- `output_contract` 的全部字段逐一回填；case 未提供 contract 时使用默认主结果字段；
- 标准字段：`status`、`sources`、`uncertainty`、`change_log`、`errors`、`degradations`、`pending_human`、`runtime`。

`status` 取值：`ok`、`partial`、`insufficient_evidence`、`input_error`。

默认主结果字段（case 提供 contract 则以 contract 为准，并补齐下列语义字段）：
- `research_question`：澄清后的问题、模式（known_item/topic_discovery）、源语言、目标语种、范围；
- `terminology_table`：术语对齐表（源词、语种、变体、类型、依据、验证方式）；
- `queries`：逐语种查询式，含翻译服务与版本、原文-译文对照；
- `results`：候选文献（标题、作者、年份、期刊/会议、DOI/URL、语言、版本、来源、相关性依据）；
- `evidence_map`：每条关键结论/数值/命中的定位（locator）+ 逐字 quote；
- `dedup`：同论文多语版本去重判定与依据；
- `missing`/`conflicts`：遗漏、无结果、冲突、权限问题，逐条说明。

可编辑副产物（`terminology_table.md`、`queries.md` 等）与 `result.json` 一并放 `/app/output/`。

## 4. 执行流程（总预算 900 秒、64 次模型请求）
第 0 步必须最先执行，立即落盘结果骨架：
```
python3 -B scripts/main.py init --input /input --output /app/output --work /app/work
```
该命令发现输入、解析 `case_id` 与契约，原子写入合法 `result.json` 骨架（全部契约字段、`status:"partial"` 初值、空结果列表）。任何后续异常都不会留下不完整产物。必须在第 3 分钟（180 秒）内完成。

之后按步骤增量改进，每步把新证据 patch 回 `result.json`：
```
python3 -B scripts/main.py patch --output /app/output --work /app/work --patch <补丁>.json [--log "步骤说明"]
```

| 步骤 | 时间窗 | 输入 | 输出 |
|---|---|---|---|
| 0 初始化 | 0–60s | /input 目录 | result.json 骨架 + work/state.json |
| 1 读题定范围 | 0–180s | case.json、task.md | research_question（模式、源/目标语种、范围） |
| 2 术语对齐 | 60–300s | 研究问题、材料、术语 | terminology_table（逐语种，含 type/basis/verification） |
| 3 逐语种检索 | 120–600s | queries | 每条命中 + 来源 + 逐字证据 |
| 4 语言/版本识别 | 300–720s | results | 每条 language/version + dedup 判定 |
| 5 对齐与误译复核 | 480–840s | 命中与原文 | evidence_map、conflicts、missing |
| 6 收尾自检 | 720–900s | 全部产物 | validate 通过 + change_log + 最终 status |

每步准则：
- 翻译与查询必须记录翻译服务与版本，保留原文-译文对照；未记录来源的译文不得当作已核对；
- 检索用 web_search/paper_search，逐语种分别构造查询；known_item 模式优先构造"题录指纹"查询（作者+年份+标题特征词）；
- 每条命中必须有 locator（URL/DOI/材料段落定位）与逐字 quote，缺失标 `pending_human`；
- 语言判定写依据（正文语言、标题语言、摘要语言、双语刊声明、出版方翻译声明）；版本判定为 original/translation/bilingual/abstract_only/undetermined；
- 冲突信息不隐藏：同一指标来源不一致时两个都记录并标 `source_conflict`。

## 5. 评分维度对齐（每条评分点 → 流程与自检）
| 评分维度 | 分值 | 落实到本 Skill |
|---|---|---|
| 实际效果 40 | 完成度15 | 步骤 1–5 产出 research_question/terminology_table/queries/results/evidence_map |
| | 正确性15 | 步骤 4 语言与版本标注；步骤 2 术语与原文用词一致；步骤 5 回译复核同义性 |
| | 完整性5 | 步骤 4 dedup 去重；步骤 6 检查声明语种均有结果、关键非英语覆盖 |
| | 可核验性5 | 每条命中 locator+quote；query 记翻译服务与版本 |
| 使用增益 25 | 质量/效率/可验证 | 术语表驱动查询生成、复用术语表、去重计数；change_log 记录增量与对照 |
| 创新增益 15 | 新增能力/方法/证据 | 语言识别与路由、版本归并、回译误译复核；method.md 说明边界与价值 |
| 可靠体验 10 | 调用/稳定/异常 | frontmatter+相对路径；原子写+增量 patch；第 7 节降级策略 |
| 生态贡献 10 | 文档/复用/分享 | SKILL.md+method.md+checklist.md；术语表模板、查询模板、去重规则可复用 |

## 6. 自检清单（对应评分维度，逐条勾选）
- [ ] `case_id` 已解析并写入；
- [ ] `output_contract` 全部字段已回填，缺值用 `null`/`[]`，未改名、未删键；
- [ ] `status` 诚实（ok/partial/insufficient_evidence/input_error），未编造数据；
- [ ] 研究问题、模式、源语言、目标语种、检索范围已明确；
- [ ] `terminology_table` 存在，每条含 type、basis、verification；
- [ ] 逐语种查询已记录，含翻译服务与版本、原文-译文对照；
- [ ] 每条结果有来源定位与逐字证据，关键非英语文献被覆盖；
- [ ] 每条命中标注语言与版本，同论文多语版本已去重并记录依据；
- [ ] `missing`/`conflicts`/权限与接口异常已如实说明；
- [ ] 术语表、查询式、语种判断依据可被第三方独立复核；
- [ ] `result.json` 是合法 UTF-8 JSON，`scripts/main.py validate` 无 issues；
- [ ] 骨架在 3 分钟内落盘；同一输入重复运行结果一致（无随机性）。

## 7. 降级策略（不得编造）
- 缺 `case.json`/`task.md` → `status=input_error`，列出缺口，退出码 0；
- 坏 JSON/坏编码 → 记入 `errors`/`degradations`，继续处理其余材料；
- 网络或检索工具不可用 → `insufficient_evidence` 或 `partial`，明确已完成与未完成；
- 翻译服务失败/限额 → 回退纯术语表查询，未验证译文标 `unverified_translation`；
- 语种无法确定 → `language:"undetermined"`，写入 `uncertainty`；
- 原文不可得或仅摘要 → 版本标 `abstract_only` 或 `undetermined`，不冒充全文；
- 材料缺失关键数值 → `not_reported` 或 `pending_human`，不补造；
- 任何失败都要留下合法的最小产物，不让进程崩溃；同一命令失败最多重试 2 次，之后降级。

## 8. 脚本命令
- `python3 -B scripts/main.py init ...`：发现输入、写骨架（默认 /input、/app/output、/app/work）；
- `python3 -B scripts/main.py patch --patch p.json [--log msg]`：深合并补丁（列表整体替换、字典递归合并），可选追加 change_log；
- `python3 -B scripts/main.py validate`：校验契约字段与 JSON 合法性，输出缺失清单；
- `python3 -B scripts/main.py status`：摘要当前进度与 change_log；
- `python3 -B scripts/selftest.py`：跑空输入/坏 JSON/带 contract 三类自检并逐项输出 PASS/FAIL。

方法规则见 `references/method.md`，逐条自检见 `references/checklist.md`。脚本仅依赖 Python 标准库。

---
name: sci-intro-writing-uncertainty
description: 为科研论文撰写“引言与相关工作”：按研究背景—相关工作—研究空白—贡献四段组织，逐条论断绑定来源证据（locator+逐字 quote），并把降级与不确定性显式化——材料不足、来源冲突、作者判断分别标注，缺数据不编造。当需要依据科研材料起草、改写、补引用或核对论文引言与相关工作并保留来源时使用。
version: 1.1.0
---

# 引言与相关工作写作 Skill（降级与不确定性变体）

## 1. 角色与何时使用

本 Skill 把“依据科研材料撰写/改写论文引言与相关工作”沉淀为可复核流程，并强化**降级与不确定性显式化**。适用指令：写引言、整理相关工作、补引用、核对贡献、把综述式罗列改写为分组比较。

核心目标五项：立得住（背景指向具体未解决问题）、分得清（相关工作按问题/方法/路线分组比较）、说得实（空白由文献缺口推出）、对得上（贡献对应正文方法/图表/结果）、**可降级（缺什么、为什么、影响范围写清，不编造）**。

不适用：纯聊天问答、期刊排版模板（LaTeX/Word 版式）、无真实材料却要求生成完整综述。

## 2. 输入契约（先发现，再假设）

运行时输入目录固定 `/input`，工作目录 `/app/work`，产物目录 `/app/output`。必须主动扫描，不假设文件名/键名：

- `/input/case.json`：案例任务、受众/文体/格式约束、`output_contract`（可能位于顶层或嵌套在 `output_contract`/`output`/`expected_output`/`fields`/`required_fields` 下）。
- `/input/task.md`：题目要求与评分说明。
- 其余材料：`materials/`、`notes.md`、`paper.md`、PDF、`.bib` 等，按实际内容读取；PDF 用平台 `pdftotext`/解析工具，不臆测提取层。

约束：单文件 >2MB 跳过并记入 `degradations`；坏 JSON/坏编码降级读取并记录；材料冲突不隐藏，两条都记录并标 `source_conflict`。

## 3. 输出契约

最终必须写 `/app/output/result.json`，合法 JSON，且**先落 partial 骨架再增量完善**：

- `case_id`：来自 case.json（缺失则 `null` 并记 `input_error`）。
- 回填 `output_contract` 的**全部字段**：字段名原样作为 result.json 顶层键，不改名、不删键；值为对象按子键结构回填，值为列表回填列表；未产出填 `null` 或 `[]`。
- 标准附加字段（必须有）：`status`、`main_product`、`sources`、`uncertainty`、`limitations`、`pending_human`、`change_log`、`artifacts`、`errors`、`degradations`、`runtime`。
  - `status` ∈ `ok | partial | insufficient_evidence | input_error`，诚实取值。
  - `sources`：数组，每项 `{key, locator, quote, kind}`；`quote` 为材料逐字片段，`locator` 形如 `materials/paper.md#p3`，`kind` ∈ `quote | paraphrase | inferred`。
  - `uncertainty`：数组，每项 `{item, reason, confidence}`；记录材料不足/冲突/未证实处。
  - `limitations`：数组，每项 `{scope, missing, impact, disposition}`；本变体的核心字段，缺数据时如实说明“缺什么、影响什么、如何处置”。
  - `change_log`：数组，每项 `{at, target, before, after, reason}`，可回退。

同目录交付可编辑主产物与佐证文件（文件名按案例要求，缺省如下）：

- `manuscript.md`：可直接进入论文的四段成稿；事实句挂 `[S#]`，材料外判断挂 `[作者判断]`，缺材料处写 `MATERIAL GAP` 或 `not_reported`。
- `evidence_map.json`：`[S#]` → `{locator, quote, kind}`，逐条可回溯。
- `change_log.json`：组装/改写与数值单位保全清单。

## 4. 执行流程（900 秒 / 64 次模型请求）

按“先落骨架、后增量改进”推进；每个模型回合前先读已写文件，避免重复劳动。**前 3 分钟（180 秒）内必须先落盘一版 partial 骨架**，此后只做增量改进，任何中途异常都不会留下空产物。

1. **扫描（0–90 秒，≤2 次请求）**：运行 `python3 -B scripts/main.py scan --input /input --work /app/work`，得到 `input_manifest.json`（材料清单+locator+合同字段）。同时通读 case.json 与 task.md，核对合同字段与 case_id。
2. **立骨架（≤180 秒，≤2 次请求）**：运行 `python3 -B scripts/main.py emit --work /app/work --output /app/output`，先落盘 `result.json`（`status=partial`，合同字段为 `null`）。若写盘失败，用最小合法 `result.json`（`status=input_error`+`errors`）收尾。
3. **建事实池与分组（120–240 秒，6–12 次请求）**：逐份读材料，摘录事实/数字/引用到 `work/facts.md`，每条带 locator+逐字 quote 与置信级；相关工作按问题设定/方法路线/数据与评价方式分组。
4. **起草四段（180–360 秒，12–24 次请求）**：写 `manuscript.md`（背景、相关工作、空白、贡献）；事实句挂 `[S#]`，引用落在具体论断；同步写 `evidence_map.json` 与 `change_log.json`。
5. **核对与回填（60–180 秒，6–12 次请求）**：逐条核对数字/单位/限定词/引用元数据；空白须由文献缺口推出，贡献须对应正文方法/图表/结果；把结果写入 `result.json` 合同字段与 `limitations`/`uncertainty`。
6. **校验与封存（≤60 秒，1–2 次请求）**：运行 `python3 -B scripts/main.py validate --work /app/work --output /app/output`；按报告修正；`status` 依完成度取 `ok`/`partial`/`insufficient_evidence`；再写一次完整 `result.json`（`runtime.phase` 置 `final`）。

请求预算只用于必要读取与写作；重复读取用 `work/` 缓存，不逐回合重读大 PDF。

## 5. 写作与证据规则（详见 references/writing_rules.md）

- 事实句必有 `[S#]` 与来源；数字/单位/统计量逐字复用，材料没有的不补，写 `MATERIAL GAP` 或 `not_reported`。
- **置信分级**：`quote`（逐字）> `paraphrase`（转述，保留限定词与适用条件）> `inferred`（多材料归纳）> `author_judgment`（材料外，必须显式标注）。
- 相关工作组内按同一维度比较取舍，不按时间罗列；转述保留原文结论、适用条件与局限。
- 空白由已引工作的共同限制/未覆盖场景/冲突结论推出，并写清本文如何填补；无法由文献推出的判断标注 `[作者判断]`。
- 引用只用材料中真实出现的来源；元数据缺失写 `pending_human`，不造 DOI/作者/年份/评测分数。

## 6. 自检清单（交付前逐条勾选）

- [ ] 四段齐全：研究背景、相关工作、研究空白、贡献，且逻辑承接。
- [ ] 背景指向具体未解决问题，无“近年来受到广泛关注”式空转。
- [ ] 相关工作按问题/方法/技术路线分组，含问题设定、方法、数据与评价维度。
- [ ] 每条引用落在具体论断上，转述与原文一致，元数据真实可核对。
- [ ] 空白由已引文献缺口推出，并写明本文填补方式；作者判断已标注。
- [ ] 每条贡献可映射到正文方法/图表/结果，不夸大适用范围。
- [ ] 每句事实有 `[S#]`，evidence_map 的 locator+quote 逐条可回溯。
- [ ] result.json 含 case_id + 合同全部字段 + status/sources/uncertainty/limitations/change_log。
- [ ] `limitations`/`uncertainty` 显式记录所有缺口、冲突与未确认项，未用编造填充。
- [ ] 无编造数据、引用、DOI 或评测分数；重复运行结果一致。

## 7. 降级与不确定性策略（本变体重点）

- 材料不足/文献库为空 → `status=insufficient_evidence`，正文只写可支撑内容，其余标 `MATERIAL GAP`，`limitations` 逐条说明缺什么与影响。
- 引用查不到原文 → `pending_human` + `uncertainty`，不臆造元数据。
- 来源冲突 → 两条都记入 `sources` 并标 `source_conflict`，正文标 `[存疑]`，`uncertainty` 记录置信判断。
- 目标期刊格式未知 → 采用作者—年份并在 `uncertainty` 声明假设；不冒充特定格式。
- 语言切换 → 术语/数字/引用原位，仅换语，限定强度不变。
- PDF/软件不可用 → `degradations` 记录，用现有文本继续，不伪造缺失状态。
- 写盘失败 → 最小合法 result.json（`input_error`+`errors`）收尾，不让进程崩溃。

## 8. 脚本调用（相对路径，纯标准库）

```bash
python3 -B scripts/main.py scan      --input /input --work /app/work
python3 -B scripts/main.py emit      --work /app/work --output /app/output
python3 -B scripts/main.py validate  --work /app/work --output /app/output
python3 -B scripts/main.py selftest  # 或 python3 -B scripts/selftest.py
```

脚本缺省 `--input /input --output /app/output --work /app/work`；本地演示可传绝对路径。脚本只做发现/骨架/校验，正文写作由 Agent 完成。

## 9. 维护信息

- 适用：中英文科研论文引言与相关工作的起草、改写、补引用、核对。
- 支持引用格式：案例指定优先；缺省作者—年份，可改数字式（改动记 `change_log`）。
- 限制：不做期刊排版模板适配；不生成材料外事实；不自动下载或联网补参考文献。
- 依赖：仅 Python 3.9+ 标准库，无第三方包。

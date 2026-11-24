---
name: sci-intro-writing
description: 依据科研材料撰写或改写论文“引言与相关工作”。以版本化证据台账（evidence ledger）为唯一中间态，先按案例 output_contract 落盘 partial 结果骨架，再按“背景—相关工作—研究空白—贡献”四段增量成稿；每条论断、数字、引用绑定 locator 与逐字 quote，产出可复核的 manuscript.md 与 result.json。适用于写引言、整理相关工作、补引用、核对贡献与空白推导。
version: 2.0.0
---

# 引言与相关工作写作 Skill（证据台账版）

## 1. 角色与何时使用

本 Skill 把“依据科研材料撰写/改写论文引言与相关工作”沉淀为可复核、可复现的流程，核心目标四项：

- **立得住**：背景指向真实未解决问题，无“近年来受到广泛关注”式空转。
- **分得清**：相关工作按问题/方法/数据与评价维度分组比较，不按时间罗列。
- **说得实**：研究空白由已引文献的缺口推出，并写明本文如何填补。
- **对得上**：贡献与方法/图表/结果一一对应，引用落在具体论断上。

适用指令：写引言、整理相关工作、补引用、核对贡献、把综述式罗列改写为分组比较。

不适用：纯聊天问答、需要期刊排版模板（LaTeX/Word 版式）的任务、没有真实材料却要求生成完整综述的场景。

## 2. 输入契约

运行时输入目录固定为 `/input`，工作目录 `/app/work`，产物目录 `/app/output`。必须主动扫描，不假设文件名：

- `/input/case.json`：案例任务、受众/文体/格式约束，以及 `output_contract`（可能位于顶层或嵌套在 `output_contract`、`output`、`expected_output`、`fields`、`required_fields` 等键下）。
- `/input/task.md`：题目要求与评分说明。
- 其余材料：`materials/`、`notes.md`、`paper.md`、PDF、`.bib` 等，均按实际文件内容读取，不依据文件名标签假设内容；PDF 用平台提供的解析工具，不臆测提取层内容。

约束：单个材料 >2MB 跳过并记入 `degradations`；坏 JSON/坏编码降级读取并记录；材料冲突不隐藏，两条都记录并标 `source_conflict`。细节见 `references/contract.md`。

## 3. 输出契约

最终必须写 `/app/output/result.json`，且是合法 JSON：

- `case_id`：来自 case.json（缺失则 `null` 并记 warning）。
- 回填 `output_contract` 的**全部字段**：字段名原样复制为 result.json 顶层键，不改名、不删键；值为对象按其子键结构回填，值为列表回填列表；未产出/未知填 `null` 或 `[]`。
- 附加字段（必须有）：`status`、`sources`、`uncertainty`、`change_log`、`artifacts`、`pending_human`、`errors`、`degradations`、`runtime`。
  - `status ∈ ok | partial | insufficient_evidence | input_error`，诚实取值。
  - `sources`：数组，每项 `{key, locator, quote, kind, status}`；`quote` 为材料逐字片段。
  - `uncertainty`：`{item, reason}`；`change_log`：`{at, target, before, after, reason}`，可回退。

同目录还须交付可编辑主产物与佐证文件（文件名按案例要求，缺省如下）：

- `manuscript.md`：可直接进入论文的四段成稿（研究背景 / 相关工作 / 研究空白 / 贡献）。
- `evidence_map.json`：`[S#]` → `{locator, quote, kind}`（由 verify 从台账派生）。
- `change_log.json`：组装/改写记录（由 verify 从台账派生）。
- `preserved_facts.json`：数值/单位保全清单（由 verify 从台账派生）。

## 4. 执行流程（900 秒 / 64 次模型请求）

**硬性预算**：总 900 秒、64 次模型请求；**前 3 分钟内必须先落盘一版结果骨架**，此后只做增量改进。每个模型回合前先读已写文件，避免重复劳动；重复读取用 `work/` 缓存，不逐回合重读大 PDF。

1. **发现（0–90 秒，≤2 次请求）**：`python3 -B scripts/main.py discover --input /input --work /app/work --output /app/output`，得到 `work/input_manifest.json` 与 `work/contract_spec.json`。通读 case.json 与 task.md，核对合同字段与 case_id、受众/文体/格式约束。
2. **落骨架（≤3 分钟内必须完成，≤2 次请求）**：`python3 -B scripts/main.py scaffold ...`，先落盘 `result.json`（合同字段全占位、`status` 按输入诚实取值）与空台账 `work/ledger.json`。此后任何中途异常都不会留下空产物。
3. **建台账（120–240 秒，6–12 次请求）**：逐份读材料，把事实/数字/引用/贡献写进 `work/ledger.json` 的 `claims` 与 `sources`，每条带 locator + 逐字 quote；相关工作按问题/方法/数据与评价分组；冲突写 `conflicts`。
4. **起草四段（180–360 秒，12–24 次请求）**：写 `manuscript.md`，事实句挂 `[S#]`，引用落在具体论断；空白由文献缺口推出，贡献映射正文；同步把改写记录写进台账 `changes`。
5. **回填与核对（60–180 秒，6–12 次请求）**：逐条核对数字/单位/限定词/引用元数据；把结果写入 result.json 合同字段，`status` 取 `ok` 或 `partial`/`insufficient_evidence`。
6. **校验与封存（≤60 秒，1–2 次请求）**：`python3 -B scripts/main.py verify ...`，由台账派生 `evidence_map.json`、`change_log.json` 与 `preserved_facts.json`；按 `work/verify_report.json` 修正 warnings/errors；收尾回填 `runtime` 与 `artifacts` 路径。

## 5. 写作与证据规则（摘要，详见 `references/checklist.md`）

- 事实句必有 `[S#]` 与来源；数字/单位/统计量逐字复用，材料没有的不补，写 `MATERIAL GAP` 或 `not_reported`。
- 相关工作组内按同一维度比较取舍；转述保留原文结论、适用条件与局限。
- 空白由已引工作的共同限制/未覆盖场景/冲突结论推出，并写清本文如何填补；无法由文献推出的判断标注“作者判断”。
- 引用只用材料中真实出现的来源；元数据缺失写 `pending_human`，不造 DOI/作者/年份。

## 6. 自检清单（交付前逐条勾选）

脚本级：`python3 -B scripts/selftest.py`（空输入、坏 JSON、最小真实案例、确定性、预算上限，退出码 0）。人工级逐条见 `references/checklist.md`，核心项：

- [ ] 四段齐全且承接；背景指向具体未解决问题。
- [ ] 相关工作按问题/方法/技术路线分组，含问题设定、方法、数据与评价维度。
- [ ] 每条引用落在具体论断上，转述与原文一致，元数据真实可核对。
- [ ] 空白由已引文献缺口推出并写明本文填补方式；作者判断已标注。
- [ ] 每条贡献可映射到正文方法/图表/结果，不夸大适用范围。
- [ ] 每句事实有 `[S#]`，evidence_map 的 locator+quote 逐条可回溯。
- [ ] result.json 含 case_id + 合同全部字段 + status/sources/uncertainty/change_log。
- [ ] 无编造数据、引用、DOI 或评测分数；重复运行结果一致。

## 7. 降级策略

- 材料不足/文献库为空 → `status=insufficient_evidence`，正文只写可支撑内容，其余标 `MATERIAL GAP`，不示例填充。
- 引用查不到原文 → `pending_human` + `uncertainty`，不臆造元数据。
- 目标期刊格式未知 → 采用作者—年份并在 `uncertainty` 声明假设；不冒充特定格式。
- 语言切换 → 保持术语/数字/引用原位，仅换语，限定强度不变。
- 缺少软件/PDF 无法解析 → 记录到 `degradations`，用现有文本继续，不伪造缺失状态。
- 写盘失败 → 用最小合法 result.json（`status=input_error` + `errors`）收尾，不让进程崩溃。
- 材料不足/冲突/未证实处必须如实写入 `uncertainty` 或 `limitations`，不允许编造。

## 8. 脚本调用（相对路径，纯标准库，Python 3.9+）

```bash
python3 -B scripts/main.py discover --input /input --work /app/work --output /app/output
python3 -B scripts/main.py scaffold --input /input --work /app/work --output /app/output
python3 -B scripts/main.py verify   --input /input --work /app/work --output /app/output
python3 -B scripts/selftest.py      # 交付前自检，退出码 0
```

脚本缺省 `--input /input --output /app/output --work /app/work`；本地演示可传绝对路径。`main.py` 为薄 CLI 入口（<30KB），全部逻辑在 `scripts/core_impl.py`；脚本只做发现/骨架/校验，正文写作由 Agent 完成。退出码：`0` 正常，`2` 输入/结果不可解析，`3` 校验存在硬伤。

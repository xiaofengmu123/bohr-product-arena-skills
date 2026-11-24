---
name: sci-intro-related-work-writing
description: 为科研论文撰写与核对“引言与相关工作”：按研究背景—相关工作—研究空白—贡献四段组织，把每条论断、数字与引用绑定到来源证据（locator + 逐字 quote），输出可复核稿件与 result.json，并以确定性脚本保证同输入两次产物字节一致。当需要依据科研材料起草、改写、补引用、核对论文引言与相关工作并保留来源时使用。
version: 1.1.0
---

# 引言与相关工作写作 Skill（自检与确定性增强版）

## 1. 角色与何时使用

本 Skill 把“依据科研材料撰写 / 改写论文的引言与相关工作”沉淀为可复核、可复现的流程。适用指令：写引言、整理相关工作、补引用、核对贡献、把综述式罗列改写为分组比较。

四项核心目标：**立得住**（背景指向真实未解决问题）、**分得清**（相关工作按问题 / 方法 / 路线分组比较）、**说得实**（空白由文献缺口推出，作者判断显式标注）、**对得上**（贡献与方法 / 图表 / 结果一一对应）。

本版强化**自检与确定性**：`scripts/selftest.py` 独立可跑，`finalize` 收尾保证产物按键序重排、无墙钟字段，同输入两次运行字节一致。

不适用：纯聊天问答、需要期刊排版模板（LaTeX/Word 版式）的任务、没有真实材料却要求生成完整综述的场景。

## 2. 输入契约

运行时输入目录固定为 `/input`，工作目录 `/app/work`，产物目录 `/app/output`。必须主动扫描，不假设文件名：

- `/input/case.json`：案例任务、受众 / 文体 / 格式约束、`output_contract`（可能位于顶层，或嵌套在 `output_contract`、`output`、`expected_output`、`fields`、`required_fields` 等键下）。
- `/input/task.md`：题目要求与评分说明。
- 其余材料：`materials/`、`notes.md`、`paper.md`、PDF、`.bib`、`.tex` 等，按实际文件内容读取，不依据文件名标签假设内容；PDF 用平台解析工具，不臆测提取层内容。

约束：单个材料 >2MB 跳过并记入 `degradations`；坏 JSON / 坏编码降级读取并记录；材料冲突不隐藏，两条都记录并标 `source_conflict`。

## 3. 输出契约

最终必须写 `/app/output/result.json`，且是合法 JSON：

- `case_id`：来自 case.json（缺失则 `null` 并记 `input_error`）。
- 回填 `output_contract` 的**全部字段**：字段名原样复制为 result.json 顶层键，不改名、不删键；值为对象按子键结构回填 `{}`，值为列表回填 `[]`，未产出 / 未知值填 `null`。
- 附加标准字段（必须有）：`status`、`sources`、`uncertainty`、`pending_human`、`change_log`、`errors`、`degradations`、`artifacts`、`runtime`。
  - `status` ∈ `ok | partial | insufficient_evidence | input_error`，诚实取值。
  - `sources`：数组，每项 `{key, locator, quote, kind}`；`quote` 为材料逐字片段，`locator` 形如 `materials/paper.md#p3`。
  - `uncertainty`：`{item, reason}`，记录材料不足 / 冲突 / 未证实处。
  - `change_log`：`{at, target, before, after, reason}`，可回退。
  - `runtime`：只写预算常量（见 §6），不写墙钟时间。

同目录还须交付可编辑主产物与佐证文件（文件名按案例要求，缺省如下）：

- `manuscript.md`：可直接进入论文的引言与相关工作成稿，四部分齐全且彼此承接。
- `evidence_map.json`：`[S#]` → `{locator, quote, kind}`，正文每个事实句挂 `[S#]`。
- `change_log.json`：组装 / 改写记录（可回退）；数值与单位保全清单可并入 `preserved_facts.json`。

## 4. 执行流程（900 秒 / 64 次模型请求）

按“先落骨架、后增量改进”推进；每个模型回合前先读已写文件，避免重复劳动。请求预算只用于必要读取与写作；重复读取用工作区缓存（`work/`），不逐回合重读大 PDF。

1. **扫描（0–90 秒，≤2 次请求）**：运行 `python3 -B scripts/main.py scan --input /input --work /app/work`，得 `input_manifest.json`（材料清单 + locator + 合同字段）。同时通读 case.json 与 task.md，核对合同字段与 case_id。
2. **立骨架（前 3 分钟内必须完成，≤2 次请求）**：运行 `python3 -B scripts/main.py skeleton --work /app/work --output /app/output`，先落盘一版 `result.json`（`status=partial`，合同字段为 `null`/`[]`/`{}`）。此后任何中途异常都不会留下空产物。
3. **建事实池与分组（120–240 秒，6–12 次请求）**：逐份读材料，摘录事实 / 数字 / 引用到 `work/facts.md`，每条带 locator + 逐字 quote；把相关工作按问题设定 / 方法路线 / 数据与评价方式分组。
4. **起草四段（180–360 秒，12–24 次请求）**：写 `manuscript.md`——研究背景、相关工作、研究空白、贡献；事实句挂 `[S#]`，引用落在具体论断上；同步写 `evidence_map.json` 与 `change_log.json`。
5. **核对与回填（60–180 秒，6–12 次请求）**：逐条核对数字 / 单位 / 限定词 / 引用元数据；空白由文献缺口推出，贡献对应正文方法 / 图表 / 结果；把结果写入 `result.json` 合同字段。
6. **校验与封存（≤60 秒，1–2 次请求）**：运行 `python3 -B scripts/main.py finalize --work /app/work --output /app/output` 做确定性重排 + 校验；按报告修正；`status` 依完成度取 `ok` 或 `partial` / `insufficient_evidence`。

## 5. 写作与证据规则（摘要，详见 references/writing_rules.md）

- 事实句必有 `[S#]` 与来源；数字 / 单位 / 统计量逐字复用，材料没有的不补，写 `MATERIAL GAP` 或 `not_reported`。
- 相关工作组内按同一维度比较取舍，不按时间罗列；转述保留原文结论、适用条件与局限。
- 空白由已引工作的共同限制 / 未覆盖场景 / 冲突结论推出，并写清本文如何填补；无法由文献推出的判断标注“作者判断”。
- 引用只用材料中真实出现的来源；元数据缺失写 `pending_human`，不造 DOI / 作者 / 年份。

## 6. 确定性规则（本版重点）

- 脚本写入的所有 JSON 用 `sort_keys=True`、无墙钟时间戳；同输入两次运行字节一致。
- `result.json` 的 `runtime` 只写预算常量：`{"budget_seconds": 900, "budget_requests": 64, "tokens_per_response": 16384}`；禁止写入 `wall_seconds`、`elapsed_seconds`、`started_at`、`finished_at`、`timestamp`。墙钟时间由平台 trace 记录。
- 完成起草后必须运行 `scripts/main.py finalize`，把 result / evidence / change_log 按键序重排并校验，消除手写键序差异。
- 列表项按字典序排序；流程不引入随机性，若引入必须固定种子。

## 7. 自检清单（对应评分维度，交付前逐条勾选）

- [ ] 四段齐全：研究背景、相关工作、研究空白、贡献，且逻辑承接。
- [ ] 背景指向具体未解决问题，无“近年来受到广泛关注”式空转。
- [ ] 相关工作按问题 / 方法 / 技术路线分组，含问题设定、方法、数据与评价维度。
- [ ] 每条引用落在具体论断上，转述与原文一致，元数据真实可核对。
- [ ] 空白由已引文献缺口推出，并写明本文填补方式；作者判断已标注。
- [ ] 每条贡献可映射到正文方法 / 图表 / 结果，不夸大适用范围。
- [ ] 每句事实有 `[S#]`，evidence_map 的 locator + quote 逐条可回溯。
- [ ] result.json 含 case_id + 合同全部字段 + status / sources / uncertainty / change_log。
- [ ] 无编造数据、引用、DOI 或评测分数。
- [ ] 重复运行结果一致（确定性；`runtime` 无墙钟字段）。

提交前运行 `python3 -B scripts/main.py selftest`，全 PASS 再交付；详见 references/checklist.md。

## 8. 降级策略

- 材料不足 / 文献库为空 → `status=insufficient_evidence`，正文只写可支撑内容，其余标 `MATERIAL GAP`，不示例填充。
- 引用查不到原文 → `pending_human` + `uncertainty`，不臆造元数据。
- 目标期刊格式未知 → 采用作者—年份引用并在 `uncertainty` 声明假设；不冒充特定格式。
- 语言切换 → 保持术语 / 数字 / 引用原位，仅换语，限定强度不变。
- 缺少软件 / PDF 无法解析 → 记录到 `degradations`，用现有文本继续，不伪造缺失状态。
- 写盘失败 → 用最小合法 result.json（`status=input_error` + `errors`）收尾，不让进程崩溃。

## 9. 脚本调用（相对路径，纯标准库）

```bash
python3 -B scripts/main.py scan --input /input --work /app/work
python3 -B scripts/main.py skeleton --work /app/work --output /app/output
python3 -B scripts/main.py validate --work /app/work --output /app/output
python3 -B scripts/main.py finalize --work /app/work --output /app/output
python3 -B scripts/main.py selftest
python3 -B scripts/main.py run --input /input --work /app/work --output /app/output
```

脚本缺省 `--input /input --output /app/output --work /app/work`；本地演示可传绝对路径。脚本只做发现 / 骨架 / 校验 / 确定性收尾，正文写作由 Agent 完成。

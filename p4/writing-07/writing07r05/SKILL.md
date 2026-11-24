---
name: sci-intro-writing
description: 为科研论文撰写"引言与相关工作"，按研究背景—相关工作—研究空白—贡献四段组织，把每条论断、数字与引用绑定到来源证据（locator+逐字 quote），输出可复核的稿件、evidence_map、change_log 与 result.json。当需要依据科研材料起草、改写、补引用或核对论文引言与相关工作并保留来源时使用。
version: 1.1.0
---

# 引言与相关工作写作 Skill

## 1. 角色与何时使用

本 Skill 把"依据科研材料撰写或改写论文引言与相关工作"沉淀为可复核流程。
适用指令：写引言、整理相关工作、补引用、核对贡献、把综述式罗列改写为分组
比较。核心目标四项：**立得住**（背景指向真实未解决问题）、**分得清**
（相关工作按问题/方法/技术路线分组比较）、**说得实**（空白由文献缺口推出）、
**对得上**（贡献与正文方法/图表/结果一一对应）。

不适用：纯聊天问答、需要期刊排版模板（LaTeX/Word 版式）的任务、没有真实
材料却要求生成完整综述的场景。

## 2. 输入契约

运行时输入目录固定为 `/input`，工作目录 `/app/work`，产物目录 `/app/output`。
必须主动扫描，不假设文件名或键名：

- `/input/case.json`：案例任务、受众/文体/格式约束、`output_contract`（结果
  契约，可能位于顶层或嵌套在 `output_contract`、`output`、`expected_output`、
  `fields`、`required_fields` 等键下）。
- `/input/task.md`：题目要求与评分说明。
- 其余材料：`materials/`、`notes.md`、`paper.md`、PDF、`.bib` 等，按实际文件
  内容读取，不依据文件名标签假设内容；PDF 用平台提供的解析工具，不臆测提取层。

约束：单文件 >2MB 跳过并记入 `degradations`；坏 JSON/坏编码降级读取并记录；
材料冲突不隐藏，两条都记录并标 `source_conflict`。

## 3. 输出契约

最终必须写 `/app/output/result.json`，且为合法 JSON：

- `case_id`：来自 case.json（缺失则 `null` 并记 `input_error`/`degradations`）。
- 回填 `output_contract` 的**全部字段**：字段名原样复制为 result.json 顶层键，
  不改名、不删键；值为对象则按其子键结构递归回填，值为列表则回填 `[]`，
  未产出/未知值填 `null`。脚本的 `work/case_normalized.json` 是抽取结果，
  Agent 必须核实后以 case.json 为准补全。
- 附加字段（必须有）：`status`、`sources`、`uncertainty`、`pending_human`、
  `change_log`、`artifacts`、`errors`、`degradations`、`runtime`。
  - `status` ∈ `ok | partial | insufficient_evidence | input_error`，诚实取值。
  - `sources`：数组，每项 `{key, locator, quote, kind}`；`quote` 为材料逐字
    片段，`locator` 形如 `materials/paper.md#p3`。
  - `change_log`：数组，每条 `{at, target, before, after, reason}`，可回退，
    为本包重点字段，逐次修改按序追加。
  - `uncertainty`：`{item, reason}`，记录材料不足/冲突/未证实处。

同目录还须交付可编辑主产物与佐证文件（文件名按案例要求，缺省如下）：

- `manuscript.md`：可直接进入论文的引言与相关工作成稿，四部分齐全且承接。
- `evidence_map.json`：`[S#]` → `{locator, quote, kind}`，正文每个事实句挂 `[S#]`。
- `change_log.json`：组装/改写记录（可回退）；数值与单位保全清单可并入
  `preserved_facts.json`。

## 4. 执行流程（总 900 秒 / 64 次模型请求）

按"先落骨架、后增量改进"推进；每个模型回合前先读已写文件，避免重复劳动。
**前 3 分钟必须完成发现并落盘一版 partial 结果骨架，此后任何中断都不会留下
空产物，之后只做增量改进。**

1. **扫描（0–90 秒，≤2 次请求）**：`python3 -B scripts/main.py scan --input /input --work /app/work`，
   得 `input_manifest.json` + `case_normalized.json`；通读 case.json 与 task.md，
   核对合同字段与 case_id。
2. **立骨架（前 3 分钟内必须完成，≤2 次请求）**：`python3 -B scripts/main.py skeleton --work /app/work --output /app/output`，
   落盘 `result.json`（`status=partial`，合同字段为 `null`/`[]`/`{}` 占位）。
3. **建事实池与分组（120–240 秒，6–12 次请求）**：逐份读材料，摘事实/数字/
   引用到 `work/facts.md`，每条带 locator+逐字 quote；按问题设定/方法路线/
   数据与评价方式分组。
4. **起草四段（180–360 秒，12–24 次请求）**：写 `manuscript.md`，事实句挂
   `[S#]`，引用落在具体论断上；同步写 `evidence_map.json` 与 `change_log.json`。
5. **核对与回填（60–180 秒，6–12 次请求）**：逐条核对数字/单位/限定词/引用
   元数据；空白由文献缺口推出，贡献对应正文方法/图表/结果；把结果写入
   result.json 合同字段与 `change_log`。
6. **校验与封存（≤60 秒，1–2 次请求）**：`python3 -B scripts/main.py validate --work /app/work --output /app/output`；
   按报告修正；`status` 依完成度取 `ok` 或 `partial`/`insufficient_evidence`；
   再写一次完整 result.json。

请求预算只用于必要读取与写作；重复读取用 `work/` 缓存，不逐回合重读大 PDF。

## 5. 写作与证据规则（摘要，详见 references/writing_rules.md）

- 事实句必有 `[S#]` 与来源；数字/单位/统计量逐字复用，材料没有的不补，写
  `MATERIAL GAP` 或 `not_reported`。
- 相关工作组内按同一维度比较取舍，不按时间罗列；转述保留原文结论、适用条件
  与局限。
- 空白由已引工作的共同限制/未覆盖场景/冲突结论推出，并写清本文如何填补；
  无法由文献推出的判断标注"作者判断"。
- 引用只用材料中真实出现的来源；元数据缺失写 `pending_human`，不造
  DOI/作者/年份。
- `change_log` 逐次追加、可回退，是 result.json 权威版本记录。

## 6. 自检清单（对应评分维度，交付前逐条勾选）

- [ ] 四段齐全：研究背景、相关工作、研究空白、贡献，且逻辑承接。
- [ ] 背景指向具体未解决问题，无"近年来受到广泛关注"式空转。
- [ ] 相关工作按问题/方法/技术路线分组，含问题设定、方法、数据与评价维度。
- [ ] 每条引用落在具体论断上，转述与原文一致，元数据真实可核对。
- [ ] 空白由已引文献缺口推出，并写明本文填补方式；作者判断已标注。
- [ ] 每条贡献可映射到正文方法/图表/结果，不夸大适用范围。
- [ ] 每句事实有 `[S#]`，evidence_map 的 locator+quote 逐条可回溯。
- [ ] result.json 含 case_id + 合同全部字段 + status/sources/uncertainty/
  pending_human/change_log/artifacts/errors/degradations/runtime。
- [ ] `change_log` 每条含 at/target/before/after/reason，可回退。
- [ ] 无编造数据、引用、DOI 或评测分数；重复运行结果一致（无随机）。

## 7. 降级策略

- 材料不足/文献库为空 → `status=insufficient_evidence`，正文只写可支撑内容，
  其余标 `MATERIAL GAP`，不示例填充。
- 引用查不到原文 → `pending_human` + `uncertainty`，不臆造元数据。
- 目标期刊格式未知 → 采用作者—年份并在 `uncertainty` 声明假设；不冒充格式。
- 语言切换 → 术语/数字/引用原位，仅换语，限定强度不变。
- 缺软件/PDF 无法解析 → 记录到 `degradations`，用现有文本继续，不伪造状态。
- 写盘失败 → 最小合法 result.json（`status=input_error` + `errors`）收尾，不崩溃。
- 所有降级都如实标注 `limitations`/`degradations`，绝不允许编造数据或状态。

## 8. 脚本调用（相对路径，纯标准库）

```bash
python3 -B scripts/main.py scan --input /input --work /app/work
python3 -B scripts/main.py skeleton --work /app/work --output /app/output
python3 -B scripts/main.py validate --work /app/work --output /app/output
python3 -B scripts/selftest.py   # 空目录/坏JSON/嵌套契约/确定性自检
# 无子命令时等价于 scan + skeleton：任何调用都会留下合法 partial 结果
```

脚本缺省 `--input /input --output /app/output --work /app/work`；本地演示可传
绝对路径。脚本仅做发现/骨架/校验，正文写作由 Agent 完成；`main.py` 只依赖
标准库，`core_impl.py` 提供可复用函数。

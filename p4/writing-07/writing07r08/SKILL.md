---
name: sci-intro-writing-aligned
description: 为科研论文撰写、改写或核对“引言与相关工作”，并按题面五大评分维度（实际效果/使用增益/创新增益/可靠体验/生态贡献）逐条对齐流程与自检项。把每条论断、数字与引用绑定到来源证据（locator + 逐字 quote），按研究背景—相关工作—研究空白—贡献四段组织，输出可复核稿件、evidence_map、change_log 与 result.json。当需要依据科研材料起草、改写、补引用、核对论文引言与相关工作并保留来源、且要求评分维度可追溯时使用。
version: 1.0.0
---

# 引言与相关工作写作 Skill（评分维度对齐版）

## 1. 角色与何时使用

本 Skill 把“依据科研材料撰写或改写论文引言与相关工作”沉淀为可复核、可对评分维度溯源的流程。适用指令：写引言、整理相关工作、补引用、核对贡献、把综述式罗列改写为分组比较。核心目标四项：**立得住**（背景指向真实未解决问题）、**分得清**（相关工作按问题/方法/路线分组比较）、**说得实**（空白由文献缺口推出）、**对得上**（贡献与方法/图表/结果一一对应）。

设计取舍（相对通用对话模型与文献管理工具）：不做一次性问答，而是强制“先读材料再写、先落骨架再增量、起草与核对分离、证据逐字可回溯”，用机械校验（`validate`）替代人工抽查。不适用：纯聊天问答、需要期刊排版模板（LaTeX/Word 版式）的任务、没有真实材料却要求生成完整综述的场景。

## 2. 输入契约

运行时输入目录固定为 `/input`，工作目录 `/app/work`，产物目录 `/app/output`。必须主动扫描，不假设文件名：

- `/input/case.json`：案例任务、受众/文体/格式约束、`output_contract`（可能位于顶层或嵌套在 `output_contract`、`outputContract`、`output`、`expected_output`、`fields`、`required_fields` 等键下）。
- `/input/task.md`：题目要求与评分说明。
- 其余材料：`materials/`、`notes.md`、`paper.md`、PDF、`.bib` 等，按实际文件内容读取，不依据文件名标签假设内容；PDF 用平台提供的解析工具（pdf-text/render/ocr），不臆测提取层内容。

约束：单文件 >2MB 跳过并记入 `degradations`；坏 JSON/坏编码降级读取并记录；材料冲突不隐藏，两条都记录并标 `source_conflict`。首次运行 `scan` 生成 `work/input_manifest.json` 与 `work/case_normalized.json`。

## 3. 输出契约

最终必须写 `/app/output/result.json`，且保证是合法 JSON：

- `case_id`：来自 case.json（缺失则 `null` 并记 `input_error`）。
- 回填 `output_contract` 的**全部字段**：字段名原样复制为 result.json 顶层键，不改名、不删键；值为对象则按其子键结构回填，值为列表则回填列表；未产出/未知值填 `null` 或 `[]`。
- 附加字段（必须有）：`status`、`sources`、`uncertainty`、`pending_human`、`change_log`、`artifacts`、`errors`、`degradations`、`runtime`。
  - `status` ∈ `ok | partial | insufficient_evidence | input_error`，诚实取值。
  - `sources`：数组，每项 `{key, locator, quote, kind}`；`quote` 为材料逐字片段，`locator` 形如 `materials/paper.md#p3`。
  - `uncertainty`：`{item, reason}`，材料不足/冲突/未证实处。
  - `change_log`：`{at, target, before, after, reason}`，可回退。
  - `runtime`：只记预算常量（900s/64 请求/16384 tokens），**不写墙钟时间戳**。

同目录还须交付可编辑主产物与佐证文件（文件名按案例要求，缺省如下）：

- `manuscript.md`：可直接进入论文的引言与相关工作成稿，四部分齐全且彼此承接，事实句挂 `[S#]`。
- `evidence_map.json`：`[S#]` → `{locator, quote, kind}`，并含 `group_rationale`（分组维度）、`gap_derivation`（空白推导）、`contribution_map`（贡献→正文证据）。
- `change_log.json`：组装/改写记录（可回退）；数值与单位保全清单可并入 `preserved_facts.json`。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

按“先落骨架、后增量改进”推进；每个模型回合前先读已写文件，避免重复劳动。**硬性纪律：前 3 分钟（≤2 次请求）必须先落盘一版合法 partial 结果骨架，之后只做增量改进。**

| 步骤 | 时间预算 | 请求上限 | 输入 → 输出 |
|---|---|---|---|
| 1. 扫描 | 0–90s | ≤2 | 运行 `scan` 得 `input_manifest.json`+`case_normalized.json`；通读 case.json 与 task.md 核对合同字段与 case_id |
| 2. 立骨架 | ≤3min | ≤2 | 运行 `skeleton` 先落盘 `result.json`（`status=partial`，合同字段为 `null`）；此后任何中途异常都不留空产物 |
| 3. 建事实池与分组 | 120–240s | 6–12 | 逐份读材料，摘事实/数字/引用到 `work/facts.md`，每条带 locator+逐字 quote；相关工作按问题/方法/数据与评价方式分组 |
| 4. 起草四段 | 180–360s | 12–24 | 写 `manuscript.md`：研究背景、相关工作、研究空白、贡献；事实句挂 `[S#]`；同步写 `evidence_map.json` 与 `change_log.json` |
| 5. 核对与回填 | 60–180s | 6–12 | 逐条核对数字/单位/限定词/引用元数据；空白须由文献缺口推出，贡献须对应正文方法/图表/结果；回填 result.json 合同字段 |
| 6. 校验与封存 | ≤60s | 1–2 | 运行 `finalize`（确定性重排 + 复校）与 `selftest.py`；按报告修正；`status` 取 `ok`/`partial`/`insufficient_evidence`；再写一次完整 result.json |

请求预算只用于必要读写；重复读取用工作区缓存（`work/`），不要逐回合重读大 PDF。

## 5. 自检清单（逐条可勾选，维度标签对应 references/scoring_alignment.md）

- [ ] `[E1]` 四段齐全（研究背景/相关工作/研究空白/贡献）且逻辑承接。
- [ ] `[E2]` 他人工作转述与原文一致，结论、适用条件、局限逐项保留，引用元数据真实。
- [ ] `[E3]` 研究空白由已引文献的共同限制/未覆盖/冲突结论推出，并写明本文填补方式。
- [ ] `[E4]` 五要素齐全：背景、相关工作、空白、贡献、引用覆盖；方法路线、关键工作、作者/年份/来源已覆盖。
- [ ] `[E5]` 每条引用落在具体论断上，`evidence_map` 的 locator+quote 逐条可回溯。
- [ ] `[E6]` 分组依据、空白推导、贡献对照关系可独立复核。
- [ ] `[U1]` 背景指向具体未解决问题，无“近年来受到广泛关注”式空转。
- [ ] `[U2]` 引用落在具体论断上，而非文末堆引用。
- [ ] `[U3]` 前 3 分钟已落盘 partial 骨架，之后只增量。
- [ ] `[U4]` 大材料只读一次，事实池复用。
- [ ] `[U5]` `change_log` 完整可回退，数值/单位保全。
- [ ] `[U6]` `runtime` 无墙钟时间戳（确定性）。
- [ ] `[I1]` 相关工作按问题/方法/路线分组，同一维度比较取舍，非时间罗列。
- [ ] `[I2]` 每条贡献可映射正文方法/图表/结果，不夸大适用范围。
- [ ] `[I3]` 五段流水线可独立运行。
- [ ] `[I4]` `validate` 机械校验锚点覆盖与四段结构。
- [ ] `[I5]` `scripts/selftest.py` 全 PASS。
- [ ] `[I6]` 边界声明不夸大，相对通用方案的价值写明。
- [ ] `[R1]` 触发词可命中真实指令（写引言/整理相关工作/补引用/核对贡献/改写综述）。
- [ ] `[R2]` 重复运行两次结果一致（确定性）。
- [ ] `[R3]` 六类异常均有 `degradations`/`pending_human`/`errors` 记录。
- [ ] `[M1]` SKILL.md 六节齐全，frontmatter 含 name/description/version。
- [ ] `[M2]` 脚本/证据表模板/分组模板/核对清单可被他人复用。
- [ ] `[M3]` 评分维度对齐表与失败分析成文（references/scoring_alignment.md）。
- [ ] 无编造数据、引用、DOI 或评测分数。

## 6. 降级策略

- 材料不足/文献库为空 → `status=insufficient_evidence`，正文只写可支撑内容，其余标 `MATERIAL GAP`，不示例填充。
- 引用查不到原文 → `pending_human` + `uncertainty`，不臆造元数据。
- 目标期刊格式未知 → 采用作者—年份引用并在 `uncertainty` 声明假设；不冒充特定格式。
- 语言切换 → 术语/数字/引用原位，仅换语，限定强度不变。
- 缺少软件/PDF 无法解析 → 记录到 `degradations`，用现有文本继续，不伪造缺失状态。
- 单文件 >2MB → 跳过并记 `degradations`；坏 JSON/坏编码 → 记 `errors`/`degradations` 后继续。
- 写盘失败 → 用最小合法 result.json（`status=input_error` + `errors`）收尾，不让进程崩溃。
- 空白缺乏依据 → 明确标“作者判断”，不得冒充文献推出。

## 7. 脚本调用（相对路径，纯标准库）

```bash
python3 -B scripts/main.py scan --input /input --work /app/work
python3 -B scripts/main.py skeleton --work /app/work --output /app/output
# Agent 在两次脚本之间完成正文起草并回填 result.json
python3 -B scripts/main.py finalize --work /app/work --output /app/output
python3 -B scripts/selftest.py
```

脚本缺省 `--input /input --output /app/output --work /app/work`；本地演示可传绝对路径。脚本仅做发现/骨架/校验/确定性封存，正文写作由 Agent 完成。子命令：`scan`、`skeleton`、`validate`、`finalize`、`selftest`、`help`。

## 8. 评分维度对齐与边界

- 五大评分维度 → 流程/自检项/交付物的逐条映射见 `references/scoring_alignment.md`；自检标签 `[E*]/[U*]/[I*]/[R*]/[M*]` 与其同源。
- 证据溯源、分组比较、空白推导、贡献对照、起草核对分离五项能力见 `references/checklist.md`。
- 边界：不做期刊排版模板适配；不生成材料外事实；不自动联网补参考文献；不做“通用大而全”的写作系统，只覆盖引言与相关工作一类任务。
- 本 Skill 不声明任何实测得分；正式案例、白板与测评结果由平台提供或生成。

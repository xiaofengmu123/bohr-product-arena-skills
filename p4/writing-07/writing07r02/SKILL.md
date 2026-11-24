---
name: sci-intro-writing
description: 为科研论文撰写“引言与相关工作”：以研究背景—相关工作—研究空白—贡献四段组织，把每条论断、数字与引用绑定到来源证据（locator + 逐字 quote），用阶段预算与早落盘机制保证 900 秒 / 64 请求内始终留有可复核的 partial 产物，输出稿件与 result.json。当需要依据科研材料起草、改写、补引用或核对论文引言与相关工作并保留来源时使用。
version: 1.1.0
---

# 引言与相关工作写作 Skill（时间预算 + 早落盘变体）

## 1. 角色与何时使用

本 Skill 把“依据科研材料撰写或改写论文引言与相关工作”沉淀为可复核、可断点续跑的流程。适用指令：写引言、整理相关工作、补引用、核对贡献、把综述式罗列改写为分组比较。四项核心目标：

- **立得住**：背景指向真实研究脉络与具体未解决问题，不用“近年来受到广泛关注”式空转。
- **分得清**：相关工作按问题/方法/数据与评价方式分组，在同一维度比较取舍，不按时间罗列。
- **说得实**：空白由已引文献的共同限制/未覆盖场景/冲突结论推出，作者判断显式标注。
- **对得上**：每条贡献可映射到正文方法/图表/结果，不夸大适用范围；引用与论断一一对应。

不适用：纯聊天问答、期刊排版模板（LaTeX/Word 版式）适配、无真实材料却要求生成完整综述的场景。

## 2. 输入契约（先发现，不假设文件名）

运行时输入目录固定为 `/input`，工作目录 `/app/work`，产物目录 `/app/output`。主动扫描，不假设文件名或键名：

- `/input/case.json`：案例任务、受众/文体/格式约束、`output_contract`（可能位于顶层或嵌套在 `output_contract`、`output`、`expected_output`、`fields`、`required_fields` 等键下）。
- `/input/task.md`：题目要求与评分说明。
- 其余材料：`materials/`、`notes.md`、`paper.md`、PDF、`.bib` 等均按实际文件内容读取，不按文件名标签假设内容；PDF 用平台解析工具，不臆测提取层。

硬约束：单文件 >2MB 跳过并记 `degradations`；坏 JSON/坏编码降级读取并记录；材料冲突不隐藏，两条都记录并标 `source_conflict`。

## 3. 输出契约

最终必须写 `/app/output/result.json`，保证可被 `json.load` 解析：

- `case_id`：来自 case.json（缺失则 `null` 并记 `input_error`）。
- 回填 `output_contract` 的**全部字段**：字段名原样作为 result.json 顶层键，不改名、不删键；值为对象按其子键结构回填，值为列表回填列表；未产出/未知填 `null` 或 `[]`。
- 附加字段（必须有）：`status`、`sources`、`uncertainty`、`change_log`、`artifacts`、`pending_human`、`errors`、`degradations`、`runtime`。
  - `status` ∈ `ok | partial | insufficient_evidence | input_error`，诚实取值。
  - `sources`：数组，每项 `{key, locator, quote, kind}`；`quote` 为材料逐字片段，`locator` 形如 `materials/paper.md#p3`。
  - `uncertainty`：`{item, reason}`，记录材料不足/冲突/未证实处。
  - `change_log`：`{at, target, before, after, reason}`，可回退；`at` 用阶段标记（如 `P3-01`）保持可复现。

同目录还须交付可编辑主产物与佐证文件（文件名按案例要求，缺省如下）：

- `manuscript.md`：可直接进入论文的引言与相关工作成稿，四部分齐全且彼此承接。
- `evidence_map.json`：`[S#]` → `{locator, quote, kind}`，正文每个事实句挂 `[S#]`。
- `change_log.json`：组装/改写记录；数值与单位保全清单可并入 `preserved_facts.json`。

## 4. 执行流程与阶段预算（本版核心升级）

总预算 **900 秒 / 64 次模型请求**（响应含推理 ≤16384 token）。**硬性要求：前 3 分钟（T+180s）内必须先落盘一版 `result.json` 骨架（`status=partial`/`insufficient_evidence`/`input_error`），此后只做增量改进**——任何中途异常都不会留下空产物。每个模型回合前先读已写文件与 `work/budget_state.json`，避免重复劳动；每完成一个阶段用 checkpoint 命令记账。

| 阶段 | 名称 | 时间 | 请求 | 输入 → 输出（检查点） |
|---|---|---|---|---|
| P0 | 扫描 | ≤90s | ≤3 | `/input` → `work/input_manifest.json` + `work/case_normalized.json` |
| P1 | 骨架落盘 | ≤90s（累计 180s） | ≤2 | 合同字段 → `output/result.json`(v0) + `work/budget_state.json` |
| P2 | 事实池 | ≤180s | ≤12 | 逐份材料 → `work/facts.md`（每条 locator+quote） |
| P3 | 起草 | ≤240s | ≤20 | 事实池 → `manuscript.md`(v1) + `evidence_map.json` + `change_log.json` |
| P4 | 核对回填 | ≤180s | ≤16 | 核对数字/单位/引用 → `result.json` 合同字段回填 |
| P5 | 校验封存 | ≤120s | ≤4 | `work/validation_report.json` + 最终 `result.json` |

阶段之和 900s / 57 请求，预留 7 请求余量。P1 是**不可跳过的硬门槛**：先跑 `skeleton` 再写作；若 T+180s 仍未完成 P1，checkpoint 会写入 `warnings`，必须优先补骨架。断点续跑：任何阶段中断后重读 `work/` 与 `budget_state.json`，从最近未完成阶段继续，不从头重读大 PDF。

## 5. 写作与证据规则（摘要，详见 references/writing_rules.md）

- 事实句必有 `[S#]` 与来源；数字/单位/统计量逐字复用，材料没有的不补，写 `MATERIAL GAP` 或 `not_reported`。
- 相关工作组内按同一维度比较取舍；转述保留原文结论、适用条件与局限。
- 空白由已引工作的共同限制/未覆盖场景/冲突结论推出，并写清本文如何填补；无法由文献推出的判断标注“作者判断”。
- 引用只用材料中真实出现的来源；元数据缺失写 `pending_human`，不造 DOI/作者/年份。

## 6. 自检清单（对应评分维度，交付前逐条勾选）

- [ ] 四段齐全：研究背景、相关工作、研究空白、贡献，且逻辑承接。
- [ ] 背景指向具体未解决问题，无“近年来受到广泛关注”式空转。
- [ ] 相关工作按问题/方法/数据与评价方式分组，含问题设定、方法、数据与评价维度。
- [ ] 每条引用落在具体论断上，转述与原文一致，元数据真实可核对。
- [ ] 空白由已引文献缺口推出，并写明本文填补方式；作者判断已标注。
- [ ] 每条贡献可映射到正文方法/图表/结果，不夸大适用范围。
- [ ] 每句事实有 `[S#]`，evidence_map 的 locator+quote 逐条可回溯。
- [ ] result.json 含 case_id + 合同全部字段 + status/sources/uncertainty/change_log。
- [ ] 无编造数据、引用、DOI 或评测分数。
- [ ] 重复运行结果一致（确定性；`runtime` 为唯一允许的运行期测量字段）。

## 7. 降级策略（缺数据/缺软件如实标注，不编造）

- 材料不足/文献库为空 → `status=insufficient_evidence`，正文只写可支撑内容，其余标 `MATERIAL GAP`，不示例填充。
- 引用查不到原文 → `pending_human` + `uncertainty`，不臆造元数据。
- 目标期刊格式未知 → 采用作者—年份引用并在 `uncertainty` 声明假设，不冒充特定格式。
- 语言切换 → 术语/数字/引用原位，仅换语，限定强度不变。
- 缺少软件/PDF 无法解析 → 记 `degradations`，用现有文本继续，不伪造缺失状态。
- 写盘失败 → 用最小合法 result.json（`status=input_error` + `errors`）收尾，不让进程崩溃。
- 超时/超请求 → 停在最近已落盘检查点，把未完成部分写进 `limitations`/`pending_human`。

## 8. 脚本调用（相对路径，纯标准库）

```bash
python3 -B scripts/main.py scan       --input /input --work /app/work
python3 -B scripts/main.py skeleton   --work /app/work --output /app/output
python3 -B scripts/main.py checkpoint --work /app/work --phase P1_skeleton --requests 2
python3 -B scripts/main.py budget     --work /app/work      # 查看剩余预算
python3 -B scripts/main.py validate   --work /app/work --output /app/output
python3 -B scripts/main.py selftest                          # 空目录/坏JSON/缺输入自检
```

脚本缺省 `--input /input --output /app/output --work /app/work`；本地演示可传绝对路径。脚本只做发现/骨架/记账/校验，正文写作由 Agent 完成；断点续跑时先 `budget` 再按未完成阶段推进。

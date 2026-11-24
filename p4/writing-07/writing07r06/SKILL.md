---
name: sci-intro-writing
description: 为科研论文撰写"引言与相关工作"：先落结构化写作计划 plan.json（分组方案、比较维度、空白推导、贡献—证据对照），再据此成稿，并把每条论断、数字与引用绑定到来源证据（locator + 逐字 quote），输出可复核稿件、evidence_map、change_log 与 result.json。适用于依据科研材料起草、改写、补引用或核对论文引言与相关工作并保留来源的任务。
version: 2.0.0
---

# 引言与相关工作写作 Skill

## 1. 角色与何时使用

本 Skill 把"依据科研材料撰写/改写论文引言与相关工作"沉淀为"先计划、后成稿、可复核"的流程。适用指令：写引言、整理相关工作、补引用、核对贡献、把综述式罗列改写为分组比较。四项核心目标：**立得住**（背景指向真实未解决问题）、**分得清**（相关工作按问题/方法/路线分组比较）、**说得实**（空白由文献缺口推出）、**对得上**（贡献与方法/图表/结果一一对应）。

不适用：纯聊天问答；期刊排版模板（LaTeX/Word 版式）；没有真实材料却要求生成完整综述。

本版设计取舍（相对通用做法的差异）：**中间表示与可复核**。成稿前先写 `plan.json`，把"分组方案、比较维度、空白推导、贡献—证据对照、引用样式"作为结构化中间产物先行落盘并逐版更新；正文只做计划的实例化，评审可沿 plan → manuscript → evidence_map → 来源材料 四层回溯。

## 2. 输入契约

运行时输入目录固定为 `/input`，工作目录 `/app/work`，产物目录 `/app/output`。必须主动扫描，不假设文件名：

- `/input/case.json`：案例任务、受众/文体/格式约束、`output_contract`（可能位于顶层或嵌套在 `output_contract`、`output`、`expected_output`、`fields`、`required_fields` 等键下）。
- `/input/task.md`：题目要求与评分说明。
- 其余材料：`materials/`、`notes.md`、`paper.md`、PDF、`.bib` 等，均按实际文件内容读取，不依据文件名标签假设内容；PDF 用平台解析工具（如 pdftotext），不臆测提取层。

约束：单文件 >2MB 跳过并记入 `degradations`；坏 JSON/坏编码降级读取并记录；材料冲突不隐藏，两条都记录并标 `source_conflict`。

## 3. 输出契约

最终必须写 `/app/output/result.json`（合法 JSON）：

- `case_id`：来自 case.json（缺失则 `null` 并记 `input_error`）。
- 回填 `output_contract` 的**全部字段**：字段名原样复制为 result.json 顶层键，不改名、不删键；值为对象按其子键结构回填，值为列表回填列表；未产出/未知值填 `null` 或 `[]`。
- 标准字段（必须有）：`status`、`sources`、`uncertainty`、`pending_human`、`change_log`、`errors`、`degradations`、`runtime`、`artifacts`。
  - `status` ∈ `ok | partial | insufficient_evidence | input_error`，诚实取值。
  - `sources`：数组，每项 `{key, locator, quote, kind}`；`quote` 为材料逐字片段，`locator` 形如 `materials/paper.md#p3`。
  - `uncertainty`：`{item, reason}`；`change_log`：`{at, target, before, after, reason}`。

同目录还须交付（文件名按案例要求，缺省如下）：

- `manuscript.md`：可直接进入论文的引言与相关工作成稿，四部分齐全且承接，事实句挂 `[S#]`。
- `plan.json`：本版核心中间表示（分组方案、比较维度、空白推导、贡献—证据对照）。
- `evidence_map.json`：`[S#]` → `{locator, quote, kind}`。
- `change_log.json`：组装/改写记录（可回退）；数值与单位保全清单可并入 `preserved_facts.json`。

## 4. 执行流程（900 秒 / 64 次模型请求，先落骨架、后增量改进）

每步先读已写文件，避免重复劳动；重复读取用 `work/` 缓存，不逐回合重读大 PDF。

| 步骤 | 输入 | 输出 | 时间 | 请求 |
|---|---|---|---|---|
| 1. 扫描 | `/input` | `work/input_manifest.json`、`work/case_normalized.json` | 0–90s | ≤2 |
| 2. 立骨架（**前 3 分钟内必须完成**） | `work/` | `/app/output/result.json`（`status=partial`，合同字段 `null`）、`/app/output/plan.json` 骨架 | ≤180s | ≤2 |
| 3. 填计划（中间表示） | 材料 | `/app/output/plan.json` 完成版：分组方案+比较维度+空白推导+贡献对照 | 120–240s | 6–12 |
| 4. 建事实池 | 材料 | `work/facts.md`（每条带 locator+逐字 quote） | 90–180s | 4–8 |
| 5. 起草四段 | plan+facts | `manuscript.md`、`evidence_map.json`、`change_log.json` | 180–360s | 12–24 |
| 6. 核对与回填 | 全部 | 逐条核对数字/单位/限定词/引用；合同字段写入 result.json | 60–180s | 6–12 |
| 7. 校验与封存 | 产物 | `work/validation_report.json`；按报告修正；status 取 `ok/partial/insufficient_evidence`；重写完整 result.json | ≤60s | 1–2 |

**第 2 步必须无条件先落盘**：`python3 -B scripts/main.py skeleton --work /app/work --output /app/output`。此后任何中途异常都不会留下空产物；后续步骤只做增量改进（改写 result.json 与各产物），不再重建骨架。请求预算只用于必要的读取与写作。

## 5. 中间表示与可复核（本版侧重点）

`plan.json`（位于 `/app/output/`，可复核交付物）是唯一权威计划源，正文是对它的实例化。最低结构：

```json
{
  "case_id": null,
  "audience_and_format": {"audience": null, "venue": null, "language": null, "citation_style": null},
  "sections": {
    "background": {"purpose": null, "target_gap": null, "claims": []},
    "related_work": {"grouping_scheme": [], "comparison_dimensions": ["problem_setting", "method", "data", "evaluation"], "works": []},
    "gap": {"derivation": null, "basis": null, "author_judgment": []},
    "contributions": []
  },
  "decision_log": []
}
```

- `grouping_scheme`：每条 `{group, criterion, works[], comparison}`，即"按什么维度分组、组内哪些工作、同一维度如何比较"。
- `gap.derivation` 写清由哪些已引文献的**共同限制/未覆盖场景/冲突结论**推出，`basis` 指向具体文献。
- `contributions`：每条 `{claim, evidence, scope}`，`evidence` 指向本文方法/图表/结果位置。
- 关键决策记 `decision_log`（`{at, decision, reason}`），保证分组与空白推导可独立复核。

## 6. 写作与证据规则（摘要，详见 references/writing_rules.md）

- 事实句必有 `[S#]` 与来源；数字/单位/统计量逐字复用，材料没有的不补，写 `MATERIAL GAP` 或 `not_reported`。
- 相关工作组内按同一维度比较取舍，不按时间罗列；转述保留原文结论、适用条件与局限。
- 空白由已引工作的共同限制/未覆盖场景/冲突结论推出，并写清本文如何填补；无法由文献推出的判断标注"作者判断"。
- 引用只用材料中真实出现的来源；元数据缺失写 `pending_human`，不造 DOI/作者/年份。
- 语言切换：术语/数字/引用原位，仅换语，限定强度不变。

## 7. 自检清单（对应评分维度，交付前逐条勾选）

- [ ] 四段齐全：研究背景、相关工作、研究空白、贡献，且逻辑承接。
- [ ] 背景指向具体未解决问题，无"近年来受到广泛关注"式空转。
- [ ] 相关工作按问题/方法/技术路线分组，含问题设定、方法、数据与评价维度；plan.json 的分组方案与正文一致。
- [ ] 每条引用落在具体论断上，转述与原文一致，元数据真实可核对。
- [ ] 空白由已引文献缺口推出（plan.json `gap.basis` 可查），并写明本文填补方式；作者判断已标注。
- [ ] 每条贡献在 plan.json 中有 `evidence` 对应正文方法/图表/结果，不夸大适用范围。
- [ ] 每句事实有 `[S#]`，evidence_map 的 locator+quote 逐条可回溯。
- [ ] result.json 含 case_id + 合同全部字段 + status/sources/uncertainty/change_log。
- [ ] 无编造数据、引用、DOI 或评测分数。
- [ ] 重复运行结果一致（确定性；无随机成分，`runtime` 时间戳除外）。

## 8. 降级策略

- 材料不足/文献库为空 → `status=insufficient_evidence`，正文只写可支撑内容，其余标 `MATERIAL GAP`，不示例填充。
- 引用查不到原文 → `pending_human` + `uncertainty`，不臆造元数据。
- 目标期刊格式未知 → 采用作者—年份引用并在 `uncertainty` 声明假设；不冒充特定格式。
- 语言切换 → 术语/数字/引用原位，仅换语，限定强度不变。
- 缺少软件/PDF 无法解析 → 记录到 `degradations`，用现有文本继续，不伪造缺失状态。
- 写盘失败 → 用最小合法 result.json（`status=input_error` + `errors`）收尾，不让进程崩溃。

## 9. 脚本调用（相对路径，纯标准库）

```bash
python3 -B scripts/main.py scan      --input /input --work /app/work
python3 -B scripts/main.py skeleton  --work /app/work --output /app/output
python3 -B scripts/main.py validate  --work /app/work --output /app/output
python3 -B scripts/selftest.py       # 合成输入自检，退出码 0 表示通过
```

脚本缺省 `--input /input --output /app/output --work /app/work`；本地演示可传绝对路径。脚本只做发现/骨架/校验，正文写作与 plan.json 填充由 Agent 完成。交付前先跑 `scripts/selftest.py` 与一次 `validate`，两者均通过再封存。

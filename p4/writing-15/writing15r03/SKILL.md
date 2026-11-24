---
name: thesis-longdoc-writing
description: 面向“学位论文与长文档写作”的可复核写作工作流：在给定科研材料上完成章节生成/改写/校对/结构化任务，先落盘合法结果骨架再增量改进，维护提纲-论证-字数对应、术语/符号/编号/交叉引用一致性，并对每条论断、引用、改动保留来源定位与版本记录；材料不足时如实标注，不补造事实。当需要把学位论文或长文档的写作流程沉淀为可被 Agent 调用、可被他人复核的流程时使用。
version: 1.0.0
---

# 学位论文与长文档写作 Skill

## 1. 角色与何时使用

本 Skill 把长文档学术写作沉淀为“输入可解析、输出可复核、失败可解释、重复运行一致”的流程。适用于围绕给定科研材料产出一个或多个可编辑章节、改稿、引用/格式结果或结构化写作产物的任务，覆盖四个核心问题：**立得住**（章节规划对应提纲、论证链条与字数分配）、**连得上**（跨章节术语、符号、定义、编号、图表与交叉引用一致）、**改得动**（局部修改只动必要处，保留原意并记录差异）、**查得清**（每条论断、引用、改动可追溯到原文/文献/版本）。

不适用：一次性问答、无材料支撑的通用写作建议、无明确输入输出约束的泛化改写。

## 2. 输入契约

- 运行时输入固定在 `/input/case.json` 与 `/input/task.md`，材料在 `/input/` 下（`materials/`、PDF、`.md/.txt/.json` 等），实际文件名与键名不作假设，以扫描结果为准。
- `/input/case.json` 可能含 `output_contract`（或 `outputContract`/`output_schema`）与 `case_id`/`id`；`output_contract` 的全部键必须**原样回填**到结果，缺值给 `null`/`[]`，不改名、不删未知键。
- 文本一律按 `utf-8-sig` 读取并容错；单文件 >2MB 跳过并在 `degradations` 记录；PDF 优先用环境预装的 `pdftotext` 抽取，无法抽取则在 `material_gaps` 标注并继续。
- 工作目录 `/app/work`（中间产物），最终结果与可编辑产物写 `/app/output/`（含 `result.json`）。

## 3. 输出契约

`/app/output/result.json` 必须合法、完整，含以下键（详见 `references/result-schema.md`）：

- `case_id`（string）、`status`（`ok | partial | insufficient_evidence | input_error`，须诚实）。
- 题目 `output_contract` 全部字段，即三项写作交付：
  - `deliverable`：目标产物（`path` 相对 `/app/output`、`format`、`title`、`description`、`completion`、可选 `preview_path`）。
  - `fact_source_map`：事实与来源映射列表，每项 `{id, type, content, locator, quote, status, note}`；`status` ∈ `mapped|missing_source|conflict|pending_human`。
  - `version_boundary`：`{diff_summary, pending_human[], material_gaps[], exceptions[]}`。
- 再加 `sources[]`（源文件清单）、`uncertainty`（`{summary, unverified_claims[], conflicts[]}`）、`change_log[]`（逐条 `{id, scope, before, after, reason, time}`）。
- 附 `errors[]`、`degradations[]`、`runtime`（`{started, finished, elapsed_s}`）。

主产物必须“可打开、可编辑、可复现”：文本给 `.md`；图给 SVG+PDF；Office 文档另出真实 PDF/PNG 预览。任何状态下都不许留不完整产物。

## 4. 执行流程（分阶段，总预算 900 秒 / 64 次模型请求）

时间与请求为上限预算；**前 3 分钟（180 秒）内必须完成 Phase 0 并把结果骨架写盘**，此后只做增量改进，每次更新都整文件重写合法 JSON。

| 阶段 | 时间 | 请求 | 输入 → 输出 | 动作 |
|---|---|---|---|---|
| P0 发现与骨架 | 0–3 min | ≤5 | `/input/*` → `/app/output/result.json`、`deliverables/draft.md` 骨架 | 运行 `scripts/main.py --phase init`；读 case.json+task.md；判定任务类型/体裁/受众/格式/`output_contract`；立即写 `status=partial` 骨架与章节骨架提纲 |
| P1 材料盘点与证据抽取 | 3–8 min | ≤14 | 材料 → `/app/work/sources.md`、`facts.md`、`terms.md` | 逐个读材料（PDF 先转文本）；建立源文件清单、术语表/符号表、事实与数字清单（locator+逐字 quote） |
| P2 章节规划 | 8–11 min | ≤8 | 事实/术语 → `/app/work/outline.md` | 写提纲：章节顺序、每节任务/证据需求/字数预算、结论清单；对应“立得住” |
| P3 撰写/改写主产物 | 11–14.5 min | ≤24 | outline+材料 → `/app/output/deliverables/draft.md` | 写真实可用章节；每条论断绑定证据 id；局部改动记 `before/after/reason` 到 `change_log` |
| P4 一致性与证据校验 | 14.5–15 min | ≤8 | draft → consistency/evidence 报告 | 运行 `scripts/consistency.py`、`scripts/evidence.py`；修正编号/交叉引用/术语冲突；回填 `fact_source_map`；标注 `pending_human`/`material_gaps` |
| P5 收尾 | 最后 | ≤2 | 工作产物 → 最终 result.json | 运行 `scripts/main.py --phase finalize`；设 `status`（证据齐且一致为 `ok`，否则 `partial`）；补 `sources/uncertainty/change_log/runtime` |

预留 ≥3 次请求作异常兜底；任一阶段失败：保留已写盘的骨架/增量产物，在 `errors`/`degradations` 记录后继续下一可执行步骤，绝不空手退出。

## 5. 自检清单（提交前逐条核对，对应评分维度）

- [ ] 任务类型判定正确：区分生成/改写/校对/结构化，并落实体裁、受众、格式约束（实际效果-完成度）。
- [ ] 主产物是真实可编辑章节/改稿，非通用建议；提纲要求的章节、研究问题/方法/结果/讨论/局限、图表/参考文献/附录齐全（实际效果-完整性）。
- [ ] 每条数字、术语、引用、图表、结论都能回溯到 `fact_source_map` 的 `locator+quote`；未新增材料外事实（实际效果-正确性/可核验）。
- [ ] 运行 `scripts/consistency.py` 后：术语/缩写/符号唯一化，章节/图/表/式编号无缺口与重复，交叉引用目标存在，无矛盾结论与重复定义（使用增益-质量）。
- [ ] `change_log` 逐条含 `before/after/reason`，作者能看懂改了什么、为什么（使用增益-可编辑性）。
- [ ] 缺材料/缺软件/坏 JSON/超长截断/编号不连续均已在 `material_gaps`/`errors`/`degradations` 说明，未编造（可靠体验-异常处理）。
- [ ] `result.json` 可被 `scripts/main.py --phase check` 通过；重复运行同一输入结果一致（可靠体验-稳定性）。
- [ ] 脚本只用标准库、路径为相对路径、可直接 `python3` 运行（生态贡献-可复用）。

## 6. 降级策略

- 材料为空/不可读：`status=insufficient_evidence`，`deliverable` 为空结构，`material_gaps` 列出缺口，退出码 0，不抛异常、不编造内容。
- 缺 `case_id`/`output_contract`：`case_id` 取目录名兜底并标注；契约字段用本 Skill 三字段缺省结构，未知键保留为 `null`。
- PDF/Office 无法解析、`pdftotext` 不可用：记录 `degradations`，仅基于可读部分产出，明确 `partial` 边界，不假装已读全文。
- 编号/引用/结论冲突无法自证：写入 `uncertainty.conflicts` 或 `pending_human`，不擅自裁决；`status` 不标 `ok`。
- 写盘失败：降级到最小合法 JSON（只含必填键），进程不崩溃；模型请求超限：停止新增，仅完成收尾写盘。

## 7. 脚本调用（相对路径，标准库）

在 Skill 根目录（安装位置如 `/skills/weekly-skill`）下运行；脚本默认 `--input /input --output /app/output --work /app/work`，缺省参数无需显式传：

```bash
python3 scripts/main.py --phase init --input /input --output /app/output --work /app/work
python3 scripts/main.py --phase check --output /app/output
python3 scripts/main.py --phase finalize --input /input --output /app/output --work /app/work
python3 scripts/consistency.py --draft /app/output/deliverables/draft.md --out /app/work/consistency.json
python3 scripts/evidence.py --draft /app/output/deliverables/draft.md --sources /app/work/sources.json --out /app/work/evidence.json
```

每个脚本都带 `--help` 且退出码 0；具体字段与证据格式见 `references/result-schema.md`、检查清单见 `references/consistency-checklist.md`。

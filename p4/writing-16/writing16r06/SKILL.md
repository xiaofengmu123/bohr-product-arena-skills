---
name: sci-transform-writing
description: 科研成果转化写作。把论文/技术报告等源材料改写成面向指定读者与体裁、可直接使用的可编辑稿件，锁定术语、校准论断强度、保留事实与来源，并输出可逐段复核的 result.json（含证据映射与改动清单）。当需要把科研成果改写为科普文章、项目申报材料或不同文体稿件时使用。
version: 1.0.0
---

# 科研成果转化写作 Skill

## 1. 角色与何时使用

本 Skill 把"给定科研源材料 → 指定读者/体裁/篇幅 → 可编辑成品稿"沉淀为可复现流程。目标是四件事：**读得懂**（不靠背景知识也能读懂）、**留得住**（证据不被当成冗余删掉）、**说得准**（论断强度与源文一致）、**接得住**（每处删改有清单和理由）。

适用：论文摘要改科普、技术报告改申报材料、跨文体改写、校对、术语统一、双语/结构化写作。不适用：一次性随笔、无源材料的自由写作。

## 2. 输入契约

运行时输入固定为：

- `/input/case.json`：任务说明、事实底稿、受众、体裁、篇幅约束，以及 `output_contract`（可能嵌套、可能以 properties/required 形式出现）。
- `/input/task.md`：题面与补充约束；`/input/` 下还可能有 `materials/`、PDF、Markdown 等材料。
- 事实只认实际文件内容，不依据文件名或领域标签臆造；PDF 先解析真实文本，解析不到就用 OCR/渲染工具，仍失败则在 `degradations` 标注。

发现而非假设：扫描 `/input` 全目录，不假设固定文件名/键名；文本按 `utf-8-sig` 容错读取，单文件 >2MB 跳过并记录，坏 JSON/坏编码降级不崩溃。

## 3. 输出契约

最终产物写入 `/app/output/`，其中 **`result.json` 必须存在**，包含：

- `case_id`：从输入回填的真实标识（取不到则 `"unknown"` 并在 `pending_human` 说明）；
- **`output_contract` 的全部字段**：原样回填全部键，不改名、不删未知键，缺值给 `null`/`[]`；
- `status`：`ok | partial | insufficient_evidence | input_error`，必须诚实；
- `sources`：证据映射，每项含 `target_locator` + `source_locator` + 逐字 `quote`；
- `uncertainty`：材料不足、冲突、待确认项；`change_log`：逐段删改、理由与证据；
- 另附 `errors`、`degradations`、`pending_human`、`runtime_ms`。

主产物（稿件）必须是**可编辑、可复现**格式：文本用 `.md`，表格用 `.csv`/`.xlsx`，图用可编辑 `.svg` + 渲染 `.pdf`/`.png` 预览。先落一份合法 partial，再做分析，任何中途异常都不留半成品。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求 / 单响应 ≤16384 token）

**第 0–3 分钟（落骨架，约 4 次请求）**
- 运行 `python3 scripts/main.py --input /input --output /app/output --work /app/work`；
- 生成 `result.json` 骨架（status=partial）、`work/inputs_summary.json`、证据表/术语表/改动清单模板；
- 立即回写 `case_id` 与契约字段，保证**任何时刻都有合法 result.json**。

**第 3–8 分钟（读透源文，约 12 次请求）**
- 读取全部材料，抽取必须保留项、数据、术语、图表引用、论断强度；
- 填写 `work/term_table.json`（术语锁定）、`work/evidence_map.json`（逐字溯源）。

**第 8–12 分钟（成稿与增量改进，约 24 次请求）**
- 按「受众画像 → 取舍策略 → 边界标注 → 多文体映射」完成第一版成品稿；
- 逐段对照源文填写 `work/change_log.json`；术语不一致、论断升格/降格即时修正；
- 每完成一个主要段落就重写一次 `result.json`，只在骨架之上增量更新。

**第 12–15 分钟（自检与收口，约 8 次请求）**
- 运行 `python3 scripts/verify.py --output /app/output --work /app/work`，逐条过自检清单；
- 修复失败项；材料仍不足就把 `status` 降为 `insufficient_evidence`，不编造；
- 最终 `python3 scripts/main.py --phase finalize --output /app/output --work /app/work` 终检并回写 runtime。

保留约 16 次请求给重读材料与返工。**前 3 分钟必须先落盘骨架，之后只做增量改进**；重复运行同一输入结果一致。

## 5. 自检清单（提交/收口前逐条勾选）

- [ ] `python3 scripts/main.py --help` 与 `--phase finalize` 退出码 0
- [ ] `result.json` 含 `case_id`、全部契约字段、`status/sources/uncertainty/change_log`
- [ ] 成品稿满足题目指定的读者、体裁、篇幅（任务完成度）
- [ ] 数字/单位/样本量/口径/图表编号/引用与源文一致，无新增结论（正确性）
- [ ] 关键结论、限定条件、方法要点、证据均保留，只删冗余（完整性）
- [ ] 逐段对照 + 改动清单 + 删改理由 + 待确认项齐全（可核验性）
- [ ] 术语单一、缩写首次展开、论断强度分级正确（质量/一致性增益）
- [ ] 空输入/坏 JSON/坏编码/写盘失败均不崩、如实标注（稳定性/异常）
- [ ] 无伪造评测分数、无虚构 DOI/数值、无预制答案（诚实性）

详细语义清单见 `references/checklist.md`，规则见 `references/term-strength-evidence.md`。

## 6. 降级策略

- **材料不足/无法判断**：`status=insufficient_evidence`，`uncertainty` 列缺口，`pending_human` 列需人确认项；不写示例填充、不编章节。
- **冲突信息**：两个来源都记录并标 `source_conflict`，不擅自选边。
- **缺失值**：写 `not_reported`/`null`，绝不补造。
- **缺软件/缺工具**：在 `degradations` 如实说明缺了什么、影响了哪个产物，给出最小合法产物而非失败退出。
- **PDF 无文本层/扫描件**：用 OCR/渲染，仍失败则 `degradations` 记录并输出可识别的部分，绝不据文件名猜内容。
- **体裁与篇幅冲突、涉密/未发表材料**：优先遵循源文事实与用户指令，冲突写入 `uncertainty`，不擅自决定取舍。

## 7. 脚本调用（相对路径，标准库）

```bash
python3 scripts/main.py --input /input --output /app/output --work /app/work   # 落骨架
python3 scripts/main.py --phase finalize --output /app/output --work /app/work # 终检
python3 scripts/verify.py --output /app/output --work /app/work               # 自检
```

脚本只依赖 Python 标准库；缺省 `--input/--output/--work` 分别指向 `/input`、`/app/output`、`/app/work`。

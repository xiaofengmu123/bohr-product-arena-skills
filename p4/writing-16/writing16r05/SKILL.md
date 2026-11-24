---
name: research-result-translation-writing
description: 科研成果转化写作 Skill：把固定科研材料（论文/报告/数据）改写成指定受众与体裁的可编辑成品，同时保留事实、数据、图表编号、引用与论断强度，并输出可逐段复核的改动清单与证据映射。当需要在"读得懂、留得住、说得准、接得住"四个维度上完成学术/科普/技术/申报文体改写时使用。
version: 1.0.0
---

# 科研成果转化写作 Skill

## 1. 角色与何时使用

你是"科研转化写作者"：依据 /input 提供的科研材料与任务约束，交付一份**可直接使用**的完整改写稿，而不是提纲、建议或片段润色；同时为每个关键结论、数值、术语、图表引用保留来源或修改记录，供原作者逐段复核。

适用：论文摘要改科普、技术报告改申报材料、双语改写、格式化与引用规范、结构化写作等固定材料写作任务。不适用：无材料的自由创作、一次性脚本、纯问答。

四个目标：**读得懂**（缩写展开、行话解释、符合受众）、**留得住**（关键结论/数据/限定/方法/图表引用不丢，只删冗余）、**说得准**（相关≠因果、初步≠定论、术语全文一致）、**接得住**（逐段对照 + 改动清单 + 删改理由 + 待确认项）。

## 2. 输入契约

运行时固定输入：
- `/input/case.json`：case_id、任务说明、目标读者、体裁、篇幅上限、源材料引用与 `output_contract`（如存在）。
- `/input/task.md`：题目说明与运行契约。
- `/input/` 下其余文件：科研材料（.md/.txt/.json/.csv 或 .pdf；PDF 用 pdftotext 抽取正文）。

**先发现，再假设**：不假设固定文件名/键名；case.json 缺 output_contract 时用默认契约（deliverable/evidence_map/change_log/sources/uncertainty/pending_human）。文本按 utf-8-sig→utf-8→gbk→latin-1 降级读取，坏编码/坏 JSON 记录到 degradations/errors 后继续，不崩溃。

## 3. 输出契约

最终产物写到 `/app/output/`，必须包含：

1. `result.json`（必需）——至少含：
   - `case_id`（从 case.json 提取）
   - **output_contract 的全部字段**（原样回填，缺值给 null/[]，不改名、不删未知键）
   - `status`：`ok | partial | insufficient_evidence | input_error`（诚实取值）
   - `sources`、`uncertainty`、`change_log`（改动清单，每项含 locator + 逐字 quote + 理由）
   - `pending_human`、`errors`、`degradations`、`runtime`
2. 主产物：可编辑稿件（优先 `.md`；若契约要求 `.docx/.pptx` 则用 office-renderer 生成并附真实 PDF/PNG 预览）。
3. `evidence_map.json`（或并入 result.json 的 evidence_map 字段）：数字/术语/引用/图表编号/关键论断 → 源文定位 + 逐字片段。
4. `verification_report.json`：由 verify.py 生成的自检结果。

原则：**先落盘骨架，再做增量**；不新增源文没有的事实、引用或 DOI；材料不足如实标 `insufficient_evidence`。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

**第 0 步（前 3 分钟内，必须完成）**：运行 `python3 -B scripts/main.py --input /input --output /app/output --work /app/work`，落盘 result.json 骨架与 manifest.json。此步保证任何后续失败都有可复核产物。

**第 1 步（读材料，约 2-4 分钟 / 5-10 次请求）**：读 manifest.json 列出的源文（含 PDF 抽取文本），确认受众、体裁、篇幅；运行 `python3 -B scripts/evidence.py --work /app/work` 抽取数值/引用/图表/缩写/比较基准/论断强度锚点。

**第 2 步（建取舍方案，约 1-2 分钟 / 2-4 次请求）**：按 references/genre-map.md 定文体映射与保留优先级，锁定术语表，列出必须保留项。

**第 3 步（写完整初稿，约 4-6 分钟 / 10-20 次请求）**：写完整改写稿（非提纲）到 `/app/output/`，逐段对照源文，确保四项目标。长文分段处理，每段写完即保存。

**第 4 步（证据映射与改动清单，约 2-3 分钟 / 5-10 次请求）**：逐项填 evidence_map 与 change_log（locator + quote + 理由 + 待确认），材料不足/冲突/涉密/未发表写入 uncertainty 或 pending_human。

**第 5 步（自检与收口，约 2 分钟 / 2-4 次请求）**：运行 `python3 -B scripts/verify.py --work /app/work --output /app/output`；修复可修问题，无法修复的如实留在 verification_report.json；更新 result.json 的 runtime 与 status。剩余请求用于增量改进，不追求用满预算。

## 5. 自检清单（逐条勾选，对应评分维度）

- [ ] 产出是完整改写稿而非提纲（任务完成度 15）
- [ ] 数值/单位/样本量/图表编号/引用与源文一致，无新结论（正确性 15）
- [ ] 关键结论/数据/限定/方法/图表引用全保留，只删冗余（完整性 5）
- [ ] 提供逐段对照 + 改动清单 + 删改理由 + 待确认问题（可核验性 5）
- [ ] 同源文/同读者/同篇幅下，事实漂移、受众错配、术语不一致均已校验（质量增益 15）
- [ ] 有受众画像→取舍策略→边界标注→多文体映射链路（创新增益 15）
- [ ] 触发清晰、重复运行稳定、异常处理到位（可靠体验 10）
- [ ] SKILL.md/模板/术语表/验收口径/失败处理齐全（生态贡献 10）
- [ ] result.json 含 case_id + 全部契约字段 + status/sources/uncertainty/change_log（交付要求）
- [ ] verify.py 退出码 0，或缺口已如实记录（运行稳定性）

## 6. 降级策略

- 缺 case.json → status=`input_error`，result.json 记录 `case.json_not_found`，仍落盘。
- 无源材料 / 只有二进制不可读 → status=`insufficient_evidence`，列出缺口到 pending_human，**不编造**。
- 坏 JSON/坏编码 → 捕获后记录到 errors/degradations，处理其余材料。
- 缺 pdftotext/office-renderer 等软件 → 用纯文本降级产物，并在 degradations 标注"软件不可用"，不伪造软件状态。
- 数据冲突 → 两处来源都记录并标 `source_conflict`，不自作取舍。
- 写盘失败 → 写最小合法 result.json（status 诚实 + errors），不让进程崩溃。
- 每次运行固定随机种子、确定性布局；同一输入两次运行结果一致。

## 7. 脚本调用（相对路径）

```bash
python3 -B scripts/main.py     --input /input --output /app/output --work /app/work
python3 -B scripts/evidence.py --work /app/work
python3 -B scripts/verify.py   --work /app/work --output /app/output
```

脚本仅依赖 Python 标准库；main.py 负责发现与骨架、evidence.py 负责证据锚点、verify.py 负责自检。参考文件：references/genre-map.md（文体映射与论断强度）、references/checklists.md（自检清单）。

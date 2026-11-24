---
name: academic-slide-evidence
description: 把论文/研究材料组织成结构清楚、证据可追溯、可编辑且符合母版的学术汇报演示稿：页级主张、图表引用、讲稿备注、母版合规、导出渲染校验；每个关键结论强制绑定 locator 与逐字 quote，缺数据/缺软件时显式写 limitations、uncertainty 与 pending_human，绝不编造。运行时读 /input/case.json 与 task.md，产物写 /app/output/result.json。
version: 2.0.0
whenToUse: 案例要求"学术 PPT 生成与编辑"，需从固定论文/材料产出可编辑幻灯片、讲稿、页级证据映射与真实渲染件时使用。
---

# 学术汇报演示稿构建（academic-slide-evidence）

## 1. 角色与何时使用
本 Skill 指导 Agent 把固定论文或研究材料组织为结构清楚、证据可追溯、可编辑、符合母版的学术演示稿，并交付可复核产物。
适用：案例要求"学术 PPT 生成与编辑"；需产出页级主张、图表引用、讲稿/备注、可编辑演示文件与真实渲染件。
不适用：论文精读综述、科研绘图算法本身、文字润色、PPT 软件二次开发；这些只做必要支撑，不展开为交付主体。

## 2. 输入契约（运行时）
- 原始输入：`/input/case.json` 与 `/input/task.md`；论文 PDF、图、数据表、母版 .pptx 等材料位于 `/input/` 或其子目录，以实际文件为准。
- 工作区 `/app/work/`；最终产物写 `/app/output/`。
- 读取优先级：以 case.json/task.md 与实际文件内容为准，不按文件名或领域标签臆断；PDF 必须用 pdf-text-tool/pdftotext 实际提取，扫描页用 ocr-tool，图页用 pdf-render-tool 渲染。
- 关键输入：`case_id`、`output_contract`（题目要求的 result.json 字段集合）、audience/time/slide_count/template/language 等约束；缺失时按材料事实推断，并在 `uncertainty` 标注。
- 单文件 >2MB 跳过并记 `uncertainty.degradations`；坏 JSON/坏编码降级读取，进程不崩溃。

## 3. 输出契约（运行时）
必须产出 `/app/output/result.json`，顶层包含：
1. `case_id`（字符串，取自 case.json；找不到时用 `"unknown"` 并记 uncertainty）；
2. `output_contract` 声明的全部字段原样回填（缺值用 null/[]；未知键保留、不改名、不删除）；
3. 保留字段：`status`（partial/complete/failed）、`sources`、`uncertainty`（unresolved/limitations/missing_artifacts/degradations/notes）、`pending_human`、`change_log`、`version_records`、`runtime`、`self_check`。
常见交付字段还有 `presentation_file`、`preview_files`、`speaker_notes`、`page_plan`、`evidence_map`、`version_records`（以实际 output_contract 为准）。
可编辑 Office/幻灯片/示意图必须同时给出真实渲染的 PDF/PNG 预览；partial 结果必须标注局限。每条 evidence 含 `claim_kind` 与 `confidence`；每条 source 含 `ref/loc/kind/quote`。

## 4. 执行流程（单案例：总预算 900s / 64 请求 / 单响应 16384 token）
硬规则：**前 3 分钟（180s）内必须落盘一版合法 `/app/output/result.json` 骨架**（case_id + 全部契约字段 + status=partial），此后只做增量改进；除字段损坏外不得清空重来。
- 步骤 0 落骨架（0–60s，≤2 请求）：读 case.json/task.md，运行 `python3 scripts/main.py scaffold`，产出 result.json、work/case_summary.json 与 change_log 种子。
- 步骤 1 盘点材料（60–180s，≤4 请求）：列出 /input 全部文件；pdf-text-tool/pdftotext 提正文，pdf-render-tool 渲染图页，ocr-tool 处理扫描图。
- 步骤 2 主线与页计划（180–300s，≤8 请求）：抽取研究问题/方法/结果/限制；写 page_plan（每页：角色、主张、图/表引用、数据出处、字数预算）。
- 步骤 3 成稿（300–540s，≤20 请求）：有母版 .pptx 必须编辑模板并保留母版/版式，不得新建覆盖；逐页填主张+图引用+讲稿备注；数字/图注/引用编号逐字核对原文；用 `record evidence` 逐条写入，fact 必须带逐字 quote。
- 步骤 4 渲染与修复（540–720s，≤18 请求）：office-renderer/soffice 导出 PDF、逐页 PNG；`main.py check` 查空页/超长页/占位文本；font-discovery 查字体、查溢出；改动前用 `record version` 存快照。
- 步骤 5 终检与交付（720–900s，≤12 请求）：`main.py check` 修复缺口；`main.py finalize --status ...` 写终版；仅当契约字段齐、产物齐、证据齐才标 complete，否则 partial 并写明局限。
单响应 16384 token 上限：一次只处理一页或一个校验点，避免整篇长文一次塞入；优先保证骨架与关键数字正确。

## 5. 自检清单（逐条勾选，对应评分维度）
- [ ] 实际效果：可编辑演示文件存在且可打开；页数/页序符合约束；每页有叙事角色；讲稿备注齐备；PDF/PNG 预览真实生成。
- [ ] 实际效果：页上数字、图注、引用编号与原文/数据表逐字一致，无编造、无按印象取整。
- [ ] 实际效果：覆盖主线、页结构、图引用、讲解逻辑、模板约束、导出渲染六要素。
- [ ] 实际效果：evidence_map、sources、版本快照、change_log、self_check 齐全，第三方可独立复核复跑。
- [ ] 使用增益：与基础 Agent 同输入同模板可比；记录返工页数、重排次数、导出复查轮次作为效率证据。
- [ ] 使用增益：质量/效率改进以 diff、运行日志、公平对照等可核验记录支撑，不以文字自述代替。
- [ ] 创新增益：页序规划、模板槽位填充、图表数据绑定、局部编辑有明确方法与边界说明。
- [ ] 可靠体验：按本文稳定触发；综述/实验/理论三类与不同模板可复跑、不塌版。
- [ ] 可靠体验：缺图、位图无数据、槽位不足、字体缺失、超页、导出失败均有识别与处置。
- [ ] 生态贡献：使用说明、依赖、参数、页数限制、已知问题齐全；脚本/骨架/检查清单可复用。
运行 `python3 scripts/main.py selftest`（或 `python3 scripts/selftest.py`）验证脚本本体。

## 6. 证据溯源与防编造（本版核心：不编造由机制保证）
- 每个关键结论/数字/图元必须能指回材料中的逐字片段：evidence 含 `locator` + `quote`；fact 类无 quote 一律拒绝入页。
- `claim_kind` 三态：`fact`（需逐字 quote）、`inference`（需 refs；quote 可缺但须给 locator）、`design_choice`（页序/排版取舍，无需 quote，须说明理由）。
- `confidence` 四档：`confirmed`（quote 在位）、`paraphrased`（改写但数字保留、quote 记录）、`inferred`（显式推断）、`unverified`（仅入 pending_human，不上页面主张）。
- 冲突信息不隐藏：同一指标两个来源不一致时两处都记并标 `source_conflict`。
- 数字进入页面前必须经 confirmed 或 paraphrased；不虚构引用、DOI、数值，缺失写 pending_human 或 not_reported。

## 7. 降级策略（缺数据/缺软件必须如实标注，禁止编造）
- 缺论文 PDF/正文：只用可确证材料，其余页标 `limitation`，不补造段落。
- 图缺失或纯位图无法取数：只引用原图并给准确定位（图号/页码），绝不重绘假曲线、不补数值。
- 模板槽位不足：映射到最近似版式，uncertainty 记录偏差。
- 字体缺失：用 font-discovery 选最接近替换并记录，不宣称字体一致。
- 页面溢出：优先删要点，字号不低于 16pt；仍不行则拆页并记录。
- 导出失败：交付可编辑源文件+已成功部分预览，status=partial，写清失败原因与已尝试命令。
- 缺 python-pptx/soffice 等工具：回退到可编辑 Markdown/HTML 并说明，绝不伪造"渲染成功"。
- 任何不确定事实：记入 uncertainty/pending_human，不进入页面主张。
每条降级写 `uncertainty.degradations`：`{what, evidence, attempted, failure, fallback}`。

## 8. 脚本调用（相对本 Skill 根目录，运行时为 /skills/weekly-skill/）
```bash
python3 scripts/main.py scaffold [--force]
python3 scripts/main.py record source --ref "Fig 3" --loc "p.7" --kind figure --quote "…原文…"
python3 scripts/main.py record evidence --page 4 --role result --claim "…" --refs "Fig 3" --loc "p.7" --quote "…原文…" --kind fact
python3 scripts/main.py record version --name v1 --note "…" --files /app/output/deck.pptx
python3 scripts/main.py record uncertainty --kind limitation --text "…"
python3 scripts/main.py check          # 写 self_check.json，退出码 1=有缺口
python3 scripts/main.py finalize --status complete|partial [--note "…"]
python3 scripts/main.py selftest       # 或 python3 scripts/selftest.py
```
脚本只依赖 Python 标准库；目录可用 `INPUT_DIR`/`WORK_DIR`/`OUTPUT_DIR` 环境变量覆盖（默认 /input、/app/work、/app/output）；脚本不依赖当前工作目录，输入输出由环境变量/参数决定。

## 9. 边界与已知问题
- 本 Skill 不实现科研绘图算法与 PPT 软件本身；图表重绘仅在数据可复核时进行。
- 脚本负责骨架、证据记录、降级登记、终检与自检；PPTX 生成由运行时已装工具（python-pptx/office-renderer）完成。
- 正式案例、白板与评分由平台提供；本包不含预写答案或隐藏测试。
- 详细规则见 `references/evidence-rules.md` 与 `references/checklist.md`。

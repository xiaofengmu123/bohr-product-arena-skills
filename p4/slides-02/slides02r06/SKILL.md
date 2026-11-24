---
name: traceable-slide-builder
description: 把论文或研究材料组织为结构清楚、证据可追溯、可编辑且符合模板的学术汇报演示稿。核心采用结构化中间产物 plan.json / figure_model.json / evidence_map.json 作为页面与证据的单一事实来源，逐条带 locator 与逐字 quote；先落骨架后增量成稿，交付可复核的 result.json、可编辑演示文件与真实渲染预览。
version: 2.0.0
whenToUse: 案例要求“学术 PPT 生成与编辑”，需从固定论文/材料产出可编辑幻灯片、讲稿备注、页级证据映射与渲染件时使用。
---

# 可追溯学术汇报稿构建（traceable-slide-builder）

## 1. 角色与何时使用
把固定论文或研究材料组织为结构清楚、证据可追溯、可编辑且符合母版的学术演示稿。
适用：要求“学术 PPT 生成与编辑”，需交付页级主张、图表引用、讲稿备注、可编辑演示文件与渲染预览，并给出页级证据映射。
不适用：论文精读综述、科研绘图算法本身、文字润色、PPT 软件二次开发；这些只做必要支撑，不展开为交付主体。

## 2. 输入契约（运行时）
- 原始输入：`/input/case.json` 与 `/input/task.md`；论文 PDF、图、数据表、母版 `.pptx` 等材料位于 `/input/` 或其子目录，以实际文件为准。
- 工作区 `/app/work/`；最终产物写 `/app/output/`。
- 读取优先级：以 `case.json`、`task.md` 与实际文件内容为准，不用文件名或领域标签臆断；PDF 用 `pdf-text-tool`/`pdftotext` 实际提取，扫描页用 `ocr-tool`。
- 关键输入：`case_id`、`output_contract`（题目要求的 result.json 字段集合）、`audience/time/slide_count/template/language` 等约束；缺失时按材料事实推断，写入 `uncertainty`。
- 单文件文本读取上限 2MB，超限跳过并在 `degradations` 记录。

## 3. 输出契约（运行时）
必须产出 `/app/output/result.json`，顶层包含：
1. `case_id`（字符串，取自 case.json）；
2. `output_contract` 声明的全部字段（字段名原样保留；值可为字符串/对象/数组，未完成项用 null 或空结构，绝不编造）；
3. 保留字段：`status`（partial/complete/failed）、`sources`（证据定位数组）、`uncertainty`、`pending_human`、`errors`、`degradations`、`change_log`、`runtime`。
常见交付字段：`presentation_file`、`preview_files`、`speaker_notes`、`page_plan`、`evidence_map`（以实际 `output_contract` 为准）。
可编辑 Office/幻灯片/示意图必须同时给出真实渲染的 PDF/PNG 预览；部分结果必须标注局限。
骨架由 `scripts/main.py scaffold` 生成，增量记录用 `record`，逐字核对用 `check-quotes`，终检用 `validate`，合并交付用 `finalize`。脚本只依赖 Python 标准库，路径相对本 Skill 根目录（运行时 `/skills/weekly-skill/`），不依赖当前工作目录。

## 4. 核心设计：结构化中间表示（本包的主要取舍）
本包与“直接改 PPT”的打法不同：页面内容先固化为三份结构化中间产物，再据此生成演示文件，使页面可被独立复核。
- `plan.json`：页计划。每页含 `index/role/claim/bullets/figures/word_budget/notes/evidence`。
- `figure_model.json`：图/表清单。每项含 `ref/locator/caption_quote/kind/data_binding/extracted_numbers/used_in_pages`。
- `evidence_map.json`：页级证据。每项含 `id/page/kind/claim/locator/quote/status`，`quote` 为源文逐字片段。
规则：任何上页数字/图注/引用编号，必须先在 evidence_map 里有 `locator + quote`，再进入页面；图能重绘仅当源文数值有逐字 quote，否则标 `crop_cite` 或 `bitmap_no_data`。
三份中间文件是单一事实来源，演示文件从它们生成，评审可跳过 PowerPoint 直接对照中间文件复核。

## 5. 执行流程（单案例，总预算 900s / 64 次模型请求）
硬规则：**前 3 分钟（180s）内必须落盘一版 `/app/output/result.json` 骨架**（case_id + 全部契约字段 + status=partial），此后只做增量改进；除字段损坏外不得清空重来。
- 步骤 0 落骨架（0–60s，≤2 请求）：读 case.json/task.md，`python3 scripts/main.py scaffold`，产出 result.json、work/case_summary.json 与三份中间产物空骨架。
- 步骤 1 盘点材料（60–180s，≤4 请求）：列出 /input 全部文件；pdftotext 提正文，pdftoppm/pdf-render-tool 渲染图页，OCR 处理扫描图；把正文与图页定位写入 case_summary.json。
- 步骤 2 中间表示（180–330s，≤10 请求）：抽研究问题/方法/结果/限制；用 `record --kind figure` 建 figure_model，用 `record --kind evidence` 写页级证据（含逐字 quote），用 `record --kind plan` 定页序与每页叙事角色。
- 步骤 3 成稿（330–570s，≤22 请求）：若提供母版 .pptx，必须编辑模板并保留母版/版式，不得新建覆盖；按 plan.json 逐页填主张+图引用+讲稿备注；数字/图注/引用编号逐字核对；用 `check-quotes` 验证 quote 在原文中存在。
- 步骤 4 渲染与修复（570–720s，≤16 请求）：office-renderer/soffice 导出 PDF、逐页 PNG；`python3 scripts/main.py validate` 查空页/缺证据/占位文本；font-discovery 查字体、查溢出；改前先写 version_records。
- 步骤 5 终检与交付（720–900s，≤10 请求）：`validate` 修复缺口；`finalize --status complete|partial` 合并中间产物写终版；仅当契约字段齐、产物齐、证据齐时标 complete，否则 partial 并写明局限。
单响应 16384 token 上限下，一次只处理一页或一个校验点，避免整篇长文一次塞入；优先保证骨架与关键数字正确。

## 6. 自检清单（逐条勾选，对应评分维度）
- [ ] 实际效果：可编辑演示文件存在且可打开；页数/页序符合约束；每页有叙事角色；讲稿备注齐备；PDF/PNG 预览真实生成。
- [ ] 实际效果：页上数字、图注、引用编号与原文/数据表一致，无编造、无按印象取整（以 check-quotes 通过为证）。
- [ ] 实际效果：覆盖主线、页结构、图引用、讲解逻辑、模板约束、导出渲染六要素。
- [ ] 实际效果：plan/figure_model/evidence_map/version_records/self_check 齐全，第三方可独立复核复跑。
- [ ] 使用增益：与基础 Agent 同输入同模板可比；记录返工页数、重排次数、导出复查轮次作为效率证据。
- [ ] 使用增益：质量/效率改进以 diff、运行日志、公平对照等可核验记录支撑，不以文字自述代替。
- [ ] 创新增益：页序规划、模板槽位填充、图表数据绑定、局部编辑有明确方法与边界说明（见 references/schema.md）。
- [ ] 可靠体验：按本文能稳定触发；综述/实验/理论三类与不同模板可复跑、不塌版。
- [ ] 可靠体验：缺图、位图无数据、槽位不足、字体缺失、超页、导出失败均有识别与处置。
- [ ] 生态贡献：使用说明、依赖、参数、页数限制、已知问题齐全；脚本/骨架/检查清单可复用。

## 7. 降级策略（缺数据/缺软件必须如实标注，禁止编造）
- 缺论文 PDF/正文：只用可确证材料，其余页标 `limitation`，不补造段落。
- 图缺失或纯位图无法取数：只引用原图并给准确定位（图号/页码），figure_model 标 `bitmap_no_data`，绝不重绘假曲线、不补数值。
- 模板槽位不足：映射到最近似版式，并在 uncertainty 记录偏差。
- 字体缺失：用 font-discovery 选最接近替换并记录，不宣称字体一致。
- 页面溢出：优先删要点，字号不低于 16pt；仍不行则拆页并记录。
- 导出失败：交付可编辑源文件+已成功部分预览，status=partial，写明失败原因与已尝试命令。
- 缺 python-pptx/soffice 等工具：回退到可编辑 Markdown/HTML 并说明，绝不伪造“渲染成功”。
- 任何不确定事实：记入 uncertainty，不进入页面主张；材料不足时 status 为 partial 并在 pending_human 列出缺口。

## 8. 脚本调用（相对 Skill 根目录）
```bash
python3 scripts/main.py scaffold --input /input --work /app/work --output /app/output
python3 scripts/main.py record --kind figure --data '{"ref":"Fig 3","locator":"paper.pdf#p7","caption_quote":"...","data_binding":"crop_cite"}'
python3 scripts/main.py record --kind evidence --data '{"page":4,"role":"result","claim":"...","quote":"...","loc":"paper.pdf#p7"}'
python3 scripts/main.py record --kind plan --data '{"index":4,"role":"result","claim":"...","figures":["Fig 3"],"evidence":["e1"]}'
python3 scripts/main.py check-quotes --source /app/work/paper.txt --evidence /app/work/evidence_map.json
python3 scripts/main.py validate --input /input --work /app/work --output /app/output
python3 scripts/main.py finalize --status complete --input /input --work /app/work --output /app/output
python3 scripts/selftest.py
```
目录可用 `INPUT_DIR`/`WORK_DIR`/`OUTPUT_DIR` 环境变量覆盖（默认 /input、/app/work、/app/output）。

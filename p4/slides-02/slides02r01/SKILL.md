---
name: academic-slide-builder-trace
description: 把论文或研究材料组织成结构清楚、证据可追溯、可编辑且符合模板的学术演示稿：页级主张、图表引用、讲稿备注、母版合规、导出校验，每条关键结论都带 locator 与逐字 quote，并在 /app/output 交付可复核的 result.json 与产物。
version: 2.0.0
whenToUse: 案例要求"学术 PPT 生成与编辑"，需要从固定论文/材料产出可编辑幻灯片、讲稿、页级证据映射与渲染件时使用。
---

# 学术汇报演示稿构建（证据溯源强化版）

## 1. 角色与何时使用
本 Skill 指导 Agent 把固定论文或研究材料组织为结构清楚、证据可追溯、可编辑并符合模板的学术演示稿。
适用：案例要求"学术 PPT 生成与编辑"，需要产出页级主张、图表引用、讲稿/备注、可编辑演示文件与真实渲染件。
不适用：论文精读综述、科研绘图算法本身、文字润色、PPT 软件二次开发；这些只做必要支撑，不作为交付主体。

## 2. 输入契约（运行时）
- 原始输入：`/input/case.json` 与 `/input/task.md`；论文 PDF、图、数据表、母版 .pptx 等材料位于 `/input/` 或其子目录，以实际文件为准。
- 工作区 `/app/work/`；最终产物写 `/app/output/`。
- 读取优先级：以 case.json/task.md 与实际文件内容为准，不用文件名或领域标签臆断；PDF 必须用 pdf-text-tool/pdftotext 实际提取，扫描页用 ocr-tool，不使用"预提取答案层"。
- 关键输入：`case_id`、`output_contract`（题目要求 result.json 的字段集合）、`audience/time/slide_count/template/language` 等约束；缺失时按材料事实推断，并写入 `uncertainty`。
- 单文件大小上限 2MB，超限跳过并记入 change_log；坏编码用 utf-8-sig 容错，坏 JSON 降级为骨架，不崩溃。

## 3. 输出契约（运行时）
必须产出 `/app/output/result.json`，顶层字段：
1. `case_id`：字符串，取自 case.json；
2. `output_contract` 声明的全部字段（字段名原样保留，值可为字符串/对象/数组；未完成项用 null 或空结构，绝不编造）；
3. 保留字段：`status`（complete/partial/failed/insufficient_evidence/input_error）、`sources`（证据定位数组）、`evidence_map`（细粒度页级证据）、`uncertainty`（未决项与局限）、`pending_human`（需人工确认项）、`change_log`（时间/步骤/动作/原因，按时间排序）。
常见交付字段：`presentation_file`、`preview_files`、`speaker_notes`、`page_plan`、`evidence_map`、`version_records`（以实际 output_contract 为准）。
可编辑 Office/幻灯片/示意图必须同时给出真实渲染的 PDF/PNG 预览；部分结果必须标注局限，导出失败时交付源文件+已成功预览并标 partial。

## 4. 证据溯源规范（本版重点）
- 每条关键结论/数值/图元都要带 `locator` + 逐字 `quote`。locator 形如 `paper.pdf#p7`、`Fig. 3`、`Tab. 2`、`Sec. 3.2`；quote 必须是从提取正文中逐字截取的原文片段，不得改写、不得按印象复述。
- evidence_map 结构：每页一个条目，含 `page`、`role`（叙事角色）、`claim`、`refs`、`locator`、`quote`、`quote_type`（verbatim/number/paraphrase）、`quote_verified`、`confidence`、`checked`。
- quote 落盘前用 `str.find` 与提取正文比对；比对不到仍记录，但标 `quote_verified=false` 并写入 uncertainty，不静默通过。
- 同一指标两个来源不一致时，两个都记录并标 `source_conflict`，不得隐藏。
- 不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。

## 5. 执行流程（单案例，总预算 900s / 64 次模型请求）
硬规则：**前 3 分钟（180s）内必须落盘一版 `/app/output/result.json` 骨架**（case_id + 全部契约字段 + status=partial），此后只做增量改进；除字段损坏外不得清空重来。
- 步骤 0 落骨架（0–60s，≤2 请求）：读 case.json/task.md，运行 `python3 scripts/scaffold.py`，产出 result.json、work/case_summary.json、sources/change_log 种子。输入=case.json，输出=骨架。
- 步骤 1 盘点材料（60–180s，≤4 请求）：列出 /input 下全部文件；pdftotext 提正文，pdf-render-tool 渲染图页，OCR 处理扫描图。输出=正文文本、图页 PNG、带编号图注清单。
- 步骤 2 主线与页计划（180–300s，≤8 请求）：抽取研究问题、方法、结果、限制；定页序与每页叙事角色；写 page_plan（每页：角色、主张、图/表引用、数据出处、字数预算）。
- 步骤 3 成稿（300–540s，≤20 请求）：若 case 提供母版 .pptx，必须编辑模板并保留母版/版式，不得新建覆盖；逐页填主张+图引用+讲稿备注；数字/图注/引用编号逐字核对原文，每条结论用 `record.py evidence` 写 locator+quote。
- 步骤 4 渲染与修复（540–720s，≤18 请求）：office-renderer/soffice 导出 PDF、逐页 PNG；`python3 scripts/selftest.py check` 查空页/超长页/占位文本；font-discovery 查字体、查溢出；改动前 `record.py version` 存快照。
- 步骤 5 终检与交付（720–900s，≤12 请求）：`selftest.py check` 修复缺口；`selftest.py finalize` 写终版；仅当契约字段齐、产物齐、证据齐时标 complete，否则 partial 并写明局限。
单响应 16384 token 上限下，一次只处理一页或一个校验点；优先保证骨架与关键数字正确。

## 6. 自检清单（交付前逐条勾选）
- [ ] 实际效果：可编辑演示文件存在且可打开；页数/页序符合约束；每页有叙事角色；讲稿备注齐备；PDF/PNG 预览真实生成。
- [ ] 实际效果：页上数字、图注、引用编号与原文/数据表一致，无编造、无按印象取整。
- [ ] 实际效果：每条关键结论都有 locator + 逐字 quote，quote 经 str.find 核验或标 unverified。
- [ ] 实际效果：覆盖主线、页结构、图引用、讲解逻辑、模板约束、导出渲染六要素。
- [ ] 实际效果：evidence_map、sources、版本快照、change_log、self_check 齐全，第三方可独立复核复跑。
- [ ] 使用增益：同输入同模板可比；记录返工页数、重排次数、导出复查轮次作为效率证据。
- [ ] 使用增益：质量/效率改进以 diff、运行日志、公平对照等可核验记录支撑，不以文字自述代替。
- [ ] 创新增益：页序规划、模板槽位填充、图表数据绑定、局部编辑、locator+quote 证据绑定有明确方法与边界说明。
- [ ] 可靠体验：按本文能稳定触发；综述/实验/理论三类与不同模板可复跑、不塌版。
- [ ] 可靠体验：缺图、位图无数据、槽位不足、字体缺失、超页、导出失败均有识别与处置。
- [ ] 生态贡献：使用说明、依赖、参数、页数限制、已知问题齐全；脚本/骨架/检查清单可复用。

## 7. 降级策略（缺数据/缺软件必须如实标注，禁止编造）
- 缺论文 PDF/正文：只用可确证材料，其余页标 `limitation`，不补造段落。
- 图缺失或纯位图无法取数：只引用原图并给准确定位（图号/页码），绝不重绘假曲线、不补数值。
- 模板槽位不足：映射到最近似版式，并在 uncertainty 记录偏差。
- 字体缺失：用 font-discovery 选最接近替换并记录，不宣称字体一致。
- 页面溢出：优先删要点，字号不低于 16pt；仍不行则拆页并记录。
- 导出失败：交付可编辑源文件+已成功部分预览，status=partial，写明失败原因与已尝试命令。
- 缺 python-pptx/soffice 等工具：回退到可编辑 Markdown/HTML 并说明，绝不伪造"渲染成功"。
- quote 比对不到：保留原文但标 `quote_verified=false`，写入 uncertainty，不静默通过、不删除。
- 任何不确定事实：记入 uncertainty 或 pending_human，不进入页面主张。

## 8. 脚本调用（相对本 Skill 根目录，运行时为 /skills/weekly-skill/）
```bash
python3 scripts/scaffold.py
python3 scripts/record.py change --step build --action "..." --reason "..."
python3 scripts/record.py source --ref "Fig 3" --loc "论文 p.7" --kind figure --quote "..."
python3 scripts/record.py evidence --page 4 --role result --claim "..." --refs "Fig 3" --loc "p.7" --quote "..."
python3 scripts/record.py version --name v1 --note "..." --files /app/output/deck.pptx
python3 scripts/selftest.py selftest   # 自检包本体，退出码 1=有问题
python3 scripts/selftest.py check      # 校验 result.json 与产物，写 self_check.json
python3 scripts/selftest.py finalize --status complete|partial
```
脚本只依赖 Python 标准库，不依赖当前工作目录；目录可用 `INPUT_DIR`/`WORK_DIR`/`OUTPUT_DIR` 环境变量覆盖（默认 /input、/app/work、/app/output）。

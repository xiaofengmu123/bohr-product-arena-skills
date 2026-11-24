---
name: slide-deck-evidence-builder
description: 在固定 900 秒 / 64 请求预算内，把论文或研究材料组织成结构清楚、证据可追溯、可编辑且符合模板的学术演示稿：先落盘 partial 结果骨架、再按阶段预算增量成稿，页级主张 + 图引用 + 讲稿备注 + 母版合规 + 导出渲染校验，并在 /app/output 交付可复核的 result.json 与产物。
version: 2.0.0
whenToUse: 案例要求“学术 PPT 生成与编辑”，需从固定论文/材料产出可编辑幻灯片、讲稿、页级证据映射与真实渲染件时使用；尤其当预算为 900s/64 请求、要求早落盘与可复核交付时。
---

# 学术演示稿构建（slide-deck-evidence-builder）

## 1. 角色与何时使用
本 Skill 指导 Agent 在严格时间预算内，把固定论文或研究材料组织为结构清楚、证据可追溯、可编辑且符合模板的学术汇报演示稿，并交付可复核产物。
适用：案例要求“学术 PPT 生成与编辑”，需产出页级主张、图表引用、讲稿/备注、可编辑演示文件与真实渲染件，且要求 evidence map、版本记录、自检齐全。
不适用：论文精读综述、科研绘图算法本身、学术写作润色、PPT 软件二次开发；这些只做必要支撑，不展开为交付主体。

## 2. 输入契约（运行时）
- 原始输入：`/input/case.json` 与 `/input/task.md`；论文 PDF、图、数据表、母版 `.pptx` 等材料位于 `/input/` 或其子目录，以实际文件为准。
- 工作区 `/app/work/`；最终产物写 `/app/output/`（含 `result.json`）。
- 读取优先级：以 `case.json`/`task.md` 与实际文件内容为准，不用文件名或领域标签臆断；PDF 必须用 pdf-text-tool/pdftotext 实际提取，扫描页用 ocr-tool。
- 关键输入：`case_id`、`output_contract`（题目要求的 result.json 字段集合）、audience/time/slide_count/template/language 等约束；缺失时按材料事实推断并记入 `uncertainty`。

## 3. 输出契约（运行时）
必须产出 `/app/output/result.json`，JSON 顶层包含：
1. `case_id`（字符串，取自 case.json；缺失写 `"unknown"` 并标 degradation）；
2. `output_contract` 声明的全部字段（字段名原样保留；值为字符串/对象/数组，未完成项用 null 或空结构，绝不编造）；
3. 保留字段：`status`（partial/complete/failed/insufficient_evidence/input_error）、`sources`（证据定位数组）、`uncertainty`、`pending_human`、`change_log`（按时间升序）、`runtime`、`errors`、`degradations`。
常见交付字段：`presentation_file`、`preview_files`、`speaker_notes`、`page_plan`、`evidence_map`、`version_records`（以实际 output_contract 为准）。
可编辑 Office/幻灯片/示意图必须同时给出真实渲染的 PDF/PNG 预览；partial 结果必须标注局限。
证据溯源硬要求：每个关键结论/数值/图元必须指回材料的逐字片段（locator + quote），写入 `sources` 或 `evidence_map`；冲突指标并存并标 `source_conflict`。
骨架由 `scripts/scaffold.py` 生成，增量记录用 `scripts/record.py`，终检/自检用 `scripts/selftest.py`。三者只依赖 Python 标准库，调用路径相对本 Skill 根目录（运行时 `/skills/weekly-skill/`），不依赖当前工作目录。

## 4. 执行流程（单案例，总预算 900s / 64 次模型请求）
硬规则：**前 3 分钟（180s）内必须落盘一版 `/app/output/result.json` 骨架**（case_id + 全部契约字段 + status=partial），此后只做增量改进；除字段损坏外不得清空重来。`runtime.scaffold_t` 是审计痕迹。

| 阶段 | 截止秒 | 请求预算 | 输入 → 输出 |
|-|-|-|-|
| 0 落骨架 | 60 | ≤2 | 读 case.json/task.md → `scaffold.py` 写 result.json 骨架 + work/plan.json + case_summary.json |
| 1 盘点材料 | 180 | ≤4 | 列 /input 全部文件；pdftotext 提正文、pdf-render-tool 渲染图页、OCR 处理扫描图 → 正文文本、图页 PNG、带编号图注清单 |
| 2 主线与页计划 | 300 | ≤8 | 抽研究问题/方法/结果/限制；定页序与每页叙事角色 → page_plan（角色/主张/图引用/数据出处/字数预算） |
| 3 成稿 | 540 | ≤20 | 若有母版 .pptx 必须编辑模板并保留母版/版式，不得新建覆盖；逐页填主张+图引用+讲稿备注；数字/图注/引用编号逐字核对原文 → evidence_map 与 sources |
| 4 渲染修复 | 540–720 | ≤18 | office-renderer/soffice 导出 PDF、逐页 PNG；`selftest.py pptx <file>` 查空页/占位/溢出；font-discovery 查字体；改动前 `record.py version` 存快照 |
| 5 终检交付 | 720–900 | ≤12 | `selftest.py check` 修复缺口；`finalize --status ...` 写终版；仅当契约字段齐、产物齐、证据齐时标 complete，否则 partial 并写明局限 |

单响应 16384 token 上限下，一次只处理一页或一个校验点，避免整篇长文一次塞入；优先保证骨架与关键数字正确。每阶段结束用 `record.py budget --step <name>` 记账（辅助记账，平台原生 trace 为权威）。

## 5. 自检清单（逐条勾选，对应评分维度）
- [ ] 实际效果：可编辑演示文件存在且可打开；页数/页序符合约束；每页有叙事角色；讲稿备注齐备；PDF/PNG 预览真实生成
- [ ] 实际效果：页上数字、图注、引用编号与原文/数据表一致，无编造、无按印象取整
- [ ] 实际效果：覆盖主线、页结构、图引用、讲解逻辑、模板约束、导出渲染六要素
- [ ] 实际效果：evidence_map、sources、版本快照、change_log、self_check 齐全，第三方可独立复核复跑
- [ ] 使用增益：与基础 Agent 同输入同模板可比；记录返工页数、重排次数、导出复查轮次作为效率证据
- [ ] 使用增益：质量/效率改进以 diff、运行日志、公平对照等可核验记录支撑，不以文字自述代替
- [ ] 创新增益：页序规划、模板槽位填充、图表数据绑定、局部编辑有明确方法与边界说明
- [ ] 可靠体验：按本文能稳定触发；综述/实验/理论三类与不同模板可复跑、不塌版
- [ ] 可靠体验：缺图、位图无数据、槽位不足、字体缺失、超页、导出失败均有识别与处置
- [ ] 生态贡献：使用说明、依赖、参数、页数限制、已知问题齐全；脚本/骨架/检查清单可复用，`selftest.py selftest` 可跑通

## 6. 降级策略（缺数据/缺软件必须如实标注，禁止编造）
- 缺论文 PDF/正文：只用可确证材料，其余页标 `limitation`，不补造段落。
- 图缺失或纯位图无法取数：只引用原图并给准确定位（图号/页码），绝不重绘假曲线、不补数值。
- 模板槽位不足：映射到最近似版式，保留母版/版式，并在 uncertainty 记录偏差。
- 字体缺失：用 font-discovery 选最接近替换并记录，不宣称字体一致。
- 页面溢出：优先删要点，字号不低于 16pt；仍不行则拆页并记录。
- 导出失败：交付可编辑源文件+已成功部分预览，status=partial，写明失败原因与已尝试命令。
- 缺 python-pptx/soffice 等工具：回退到可编辑 Markdown/HTML 并说明，绝不伪造“渲染成功”。
- 空输入目录：`scaffold.py` 退出码 0，写 `insufficient_evidence`，列出缺口。
- 坏 JSON/坏编码：容错读取（utf-8-sig/utf-8/latin-1），记入 errors/degradations，继续处理其余材料。
- 任何不确定事实：记入 uncertainty/pending_human，不进入页面主张。

## 7. 脚本快速调用（相对 Skill 根目录）
```bash
python3 scripts/scaffold.py                     # 早落盘（步骤 0，≤60s）
python3 scripts/record.py change|source|evidence|version|contract|budget|status ...
python3 scripts/selftest.py check               # 写 self_check.json，必查项失败退出码 1
python3 scripts/selftest.py pptx /app/output/deck.pptx
python3 scripts/selftest.py finalize --status complete|partial
python3 scripts/selftest.py selftest            # 冒烟自检，证明包可运行
```
详见 `references/contracts.md` 与 `references/checklist.md`。

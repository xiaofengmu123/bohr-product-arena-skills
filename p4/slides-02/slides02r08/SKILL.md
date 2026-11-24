---
name: evidence-deck-architect
description: 把固定论文或研究材料组织为结构清晰、证据可追溯、可编辑且符合模板的学术幻灯片，交付真实可打开的 pptx 与页数一致的 PDF 预览，并按题目评分维度逐项自检、可复核回填 result.json。当案例要求“学术 PPT 生成与编辑”、需要页级主张、图表引用、讲稿备注、模板继承与导出校验时使用。
version: 1.0.0
---

# 学术幻灯片构建（evidence-deck-architect）

## 1. 角色与适用边界
本 Skill 指导 Agent 把固定论文/研究材料组织成结构清楚、证据可追溯、可编辑且符合模板的学术演示稿，并交付可复核产物。核心是取舍与重排：每页承担一个叙事角色（title/background/method/result/discussion/limitation/conclusion），一句话主张 + 2–6 条要点 + 证据定位（locator + 逐字 quote）+ 讲稿备注。
适用：学术 PPT 生成与编辑、页级计划、图表引用、模板继承、导出渲染校验。
不适用：论文精读综述、科研绘图算法本身、文字润色、PPT 软件二次开发；这些只做支撑，不展开为交付主体。

## 2. 输入契约（运行时）
- 原始输入：`/input/case.json` 与 `/input/task.md`；论文 PDF、图、数据表、母版 `.pptx` 等在 `/input/` 或其子目录，以实际文件为准，不用文件名或领域标签臆断。
- 工作区 `/app/work/`；最终产物写 `/app/output/`（含 `result.json`）。
- 读取优先级：`case.json` / `task.md` 与实际文件内容为事实依据；PDF 必须用 `pdf-text-tool`/`pdftotext` 实际提取，扫描页用 `ocr-tool`，图表用 `pdf-render-tool` 渲染页 PNG。
- 关键输入：`case_id`、`output_contract`（`result.json` 需回填的字段集合）、`audience`/`time`/`slide_count`/`template`/`language` 等约束；缺失时按材料事实推断并在 `uncertainty` 标注。

## 3. 输出契约（运行时）
必须产出 `/app/output/result.json`，JSON 顶层包含：
1. `case_id`（字符串，取自 case.json，原样回填）；
2. `output_contract` 声明的全部字段（字段名原样保留；缺值给 null/[]，不改名、不删未知键）。本题缺省契约七字段：`case_id`、`status`、`editable_source`、`export_artifact`、`content_map`、`compliance_checks`、`exceptions`；
3. 保留信封字段：`status`（ok/partial/insufficient_evidence/input_error）、`sources`（证据定位数组）、`uncertainty`、`pending_human`、`change_log`（每条含 t/step/action/reason/status，按时间排序）、`errors`、`degradations`、`runtime`。
主产物：`presentation.pptx`（可打开、可编辑）+ `presentation.pdf`（页数 == 幻灯片数）+ `evidence_map.json`（逐页 locator+quote+quote_verified）；edit 模式加 `changes.json`。
部分结果必须标注局限；任何产物不得是占位空文件。

## 4. 执行流程与预算（总 900s / 64 次模型请求）
硬规则：**前 3 分钟（180s）内必须落盘一版 `/app/output/result.json` 骨架**（case_id + 全部契约字段 + status=partial），此后只做增量改进，除字段损坏外不得清空重来。单响应 16384 token 上限，一次只处理一页或一个校验点。

- 步骤 0 落骨架（0–60s，≤2 请求）：读 case.json/task.md，`python3 scripts/main.py scaffold`，产出 result.json 骨架 + `work/index.json` + `work/draft.skeleton.json`。输入=case.json，输出=骨架。
- 步骤 1 盘点材料（60–180s，≤4 请求）：`python3 scripts/main.py survey` 列出 /input 全部文件；pdf-text-tool 提正文，pdf-render-tool 渲染图页，OCR 处理扫描图。输出=正文文本、图页 PNG、带编号图注清单。
- 步骤 2 主线与页计划（180–300s，≤8 请求）：抽取研究问题/方法/结果/限制，定页序与每页叙事角色；写 `work/draft.json`（每页 n/title/claim/role/bullets/evidence/assets/notes）。先判 mode：create（从零）/edit（改既有 deck 目标页）/adapt（换受众/场合）。
- 步骤 3 成稿（300–540s，≤20 请求）：`python3 scripts/main.py audit` 校验草稿与逐字引文命中；若 case 提供母版 .pptx，必须真继承（保留母版/版式/主题 sha256 不变），不得新建空白再贴文本框；逐页填主张+图引用+讲稿备注；数字/图注/引用编号逐字核对原文。
- 步骤 4 构建与修复（540–720s，≤18 请求）：`python3 scripts/main.py build` 生成 pptx+pdf+result.json+evidence_map.json；office-renderer/soffice 导出真实 PDF、逐页 PNG（平台工具优先，脚本文本层 PDF 仅兜底）；查空页/超长页/占位文本/字体缺失；改前 `record` 存快照。
- 步骤 5 终检与交付（720–900s，≤12 请求）：`python3 scripts/main.py finalize` 写 `selfcheck.json` 与 `dimension_map.json`；仅当契约字段齐、产物齐、证据齐时标 complete（本实现记 ok），否则 partial 并写明局限。

## 5. 评分维度对齐与自检清单（本版核心变体）
把题目评分维度逐条映射到流程与自检项；`scripts/selftest.py` 输出每条检查的 dimension 标签。

- [ ] 实际效果(40)：pptx 可打开可编辑；页数/页序符合约束；每页有 role+claim；讲稿备注齐；PDF/PNG 预览真实生成。
- [ ] 实际效果(40)：页上数字、图注、引用编号与原文/数据表逐字一致，无编造、无按印象取整。
- [ ] 实际效果(40)：覆盖主线、页结构、图引用、讲解逻辑、模板约束、导出渲染六要素；evidence_map/sources/change_log 齐全可复跑。
- [ ] 使用增益(25)：同输入同模板可比；记录返工页数、重排次数、导出复查轮次（`change_log` + `runtime`）作为效率证据。
- [ ] 使用增益(25)：质量/效率改进以 diff、运行日志、公平对照等可核验记录支撑，不以文字自述代替。
- [ ] 创新增益(15)：页序规划、模板槽位填充、图表数据绑定、局部编辑（edit 只改目标页）有明确方法与边界说明。
- [ ] 可靠体验(10)：按本文稳定触发；综述/实验/理论三类与不同模板可复跑、不塌版；重复运行结果一致（固定 ZIP 时间戳）。
- [ ] 可靠体验(10)：缺图、位图无数据、槽位不足、字体缺失、超页、导出失败均有识别与处置。
- [ ] 生态贡献(10)：使用说明、依赖、参数、页数限制、已知问题齐全；脚本/骨架/检查清单可复用。

## 6. 降级策略（缺数据/缺软件必须如实标注，禁止编造）
- 缺论文 PDF/正文：只用可确证材料，其余页标 `limitation`，不补造段落。
- 图缺失或纯位图无法取数：只引用原图并给准确定位（图号/页码），绝不重绘假曲线、不补数值。
- 模板槽位不足：映射到最近似版式并记 `uncertainty`；字体缺失：用 font-discovery 选最接近替换并记录，不宣称字体一致。
- 页面溢出：优先删要点，字号不低于 16pt；仍不行则拆页并记录。
- 导出失败：交付可编辑源文件 + 已成功部分预览，status=partial，写明失败原因与已尝试命令。
- 缺 python-pptx/soffice 等工具：回退到可编辑 Markdown/HTML 并说明，绝不伪造“渲染成功”。
- 任何不确定事实：记入 `uncertainty`，不进入页面主张。

## 7. 脚本调用（相对本 Skill 根目录）
```bash
python3 scripts/main.py scaffold                 # 前 3 分钟落 partial 骨架
python3 scripts/main.py survey                   # 盘点材料/工具
python3 scripts/main.py audit                    # 校验 work/draft.json（引文命中）
python3 scripts/main.py build                    # pptx + pdf + result.json + evidence_map
python3 scripts/main.py finalize                 # 终检 selfcheck.json + dimension_map.json
python3 scripts/selftest.py                      # 自检，退出码 1=有缺口
```
脚本只依赖 Python 标准库；调用路径相对本 Skill 根目录（运行时 `/skills/weekly-skill/`），不依赖当前工作目录；目录可用 `--input/--work/--output` 覆盖（默认 `/input`、`/app/work`、`/app/output`）。空输入/坏 JSON/写盘失败均降级写合法 result.json，退出码恒 0。

## 8. 最小调用示例（示意，非正式案例）
本地合成一个最小 case 跑通 scaffold→build→finalize：`case.json` 含 `case_id` 与 `output_contract` 七字段，`work/draft.json` 含 3 页（title/method/result），每页带 locator+quote；输出为示意内容，不代表正式案例数据。空输入目录时 scaffold 仍写合法 `result.json`（status=partial），不抛异常。

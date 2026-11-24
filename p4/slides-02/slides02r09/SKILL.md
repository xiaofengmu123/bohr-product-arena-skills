---
name: academic-slide-builder
description: 把论文或研究材料组织成结构清楚、证据可追溯、可编辑且符合模板的学术汇报演示稿；以“先落骨架、逐页增量、事实绑定、导出校验”为主线，在 /app/output 交付可复核的 result.json 与可编辑产物及真实渲染件。适用于“学术 PPT 生成与编辑”类案例。
version: 1.0.0
whenToUse: 案例要求从固定论文/材料产出可编辑幻灯片、讲稿、页级证据映射与渲染件时使用。
---

# 学术汇报演示稿构建

## 1. 角色与何时使用
本 Skill 指导 Agent 把固定论文或研究材料组织为结构清楚、证据可追溯、可编辑且符合模板的学术演示稿，并交付可复核产物。
- 适用：任务要求产出页级主张、图表引用、讲稿/备注、可编辑演示文件与真实渲染件，且每页内容能指回论文/材料定位。
- 不适用：论文精读综述、科研绘图算法本身、文字润色、PPT 软件二次开发；这些只做必要支撑，不作为交付主体。

## 2. 输入契约（运行时）
- 原始输入：`/input/case.json` 与 `/input/task.md`；论文 PDF、图、数据表、母版 `.pptx` 等材料位于 `/input/` 或其子目录，以实际文件为准。
- 工作区 `/app/work/`；最终产物写 `/app/output/`（含 `result.json`）。
- 读取优先级：以 `case.json` 实际字段与材料文件内容为准，不用文件名或领域标签臆断；PDF 必须用 `pdf-text-tool`/`pdftotext` 实际提取，扫描页用 `ocr-tool`。
- 关键输入：`case_id`、`output_contract`（result.json 的字段集合，须原样回填全部键）、`audience/time/slide_count/template/language` 等约束。
- 环境可用工具与预装软件见 `environment/resources.yaml`（pdf-text-tool、pdf-render-tool、ocr-tool、office-renderer、font-discovery 等），缺失时按第 6 节降级。
- 约束缺失时按材料事实推断，并在 `uncertainty` 标注。

## 3. 输出契约（result.json）
必须产出 `/app/output/result.json`，顶层包含：
1. `case_id`（字符串，取自 case.json）；
2. `output_contract` 声明的全部字段（字段名原样保留；未完成项填 `null` 或空结构，绝不编造值）；
3. 保留字段：`status`（`partial|complete|failed`）、`sources`（证据定位数组）、`uncertainty`（未决项与局限）、`pending_human`（需人判断项）、`change_log`（含时间/步骤/动作/原因，按时间排序）。
常见交付字段还有 `presentation_file`、`preview_files`、`speaker_notes`、`page_plan`、`evidence_map`、`version_records`（以实际 output_contract 为准）。

status 判定：
- complete：契约字段全部非 null，主产物与预览文件存在且可解析，evidence_map 与 sources 非空。
- partial：骨架与主产物存在，但有 null 契约字段/缺预览/证据缺口，uncertainty 写明局限。
- failed：无法产出主产物或运行异常，result.json 仍要写出并记录 errors。

**证据溯源（硬要求）**：每个关键结论、数值、图注、引用编号都要指回材料中的逐字片段。
事实先以“locator + 逐字 quote”冻结进 `work/facts.json`，页面只绑定事实 ID、不重打数字——从机制上避免抄错与按印象取整。
同一指标两个来源不一致时，两条都冻结并标 `source_conflict`，页面采用哪一条记入 uncertainty。

## 4. 执行流程（单案例，总预算 900s / 64 请求 / 单响应 16384 token）
硬规则：**前 3 分钟（180s）内必须落盘一版 `/app/output/result.json` 骨架**（case_id + 全部契约字段 + status=partial），此后只做增量改进；除字段损坏外不得清空重来。脚本调用路径相对 Skill 根目录（运行时 `/skills/weekly-skill/`）。

- 步骤 0 落骨架（0–60s，≤2 请求）：读 case.json/task.md，运行 `python3 scripts/main.py scaffold`，产出 result.json 骨架、`work/case_summary.json` 与 facts/evidence/sources/versions/change_log 种子。输入=case.json，输出=骨架。
- 步骤 1 盘点与事实冻结（60–180s，≤4 请求）：列出 `/input` 全部文件；`pdftotext` 提正文、`pdftoppm`/`pdf-render-tool` 渲染图页、`ocr-tool` 处理扫描图；把关键数字、图注、引用编号用 `record --kind fact` 冻结（逐字 quote + locator）。输入=材料文件，输出=正文、图页 PNG、facts.json。
- 步骤 2 主线与页计划（180–300s，≤8 请求）：抽取研究问题、方法、结果、限制；定页序与每页叙事角色；写 `page_plan`（每页：角色、主张、图/表引用、数据出处、字数预算）。输入=正文+facts，输出=page_plan。
- 步骤 3 成稿（300–540s，≤20 请求）：若有母版 `.pptx` 必须编辑模板并保留母版/版式，不得新建覆盖；逐页填主张+图引用+讲稿备注；页面数字引用事实 ID；用 `record --kind evidence|source|version` 写证据映射、来源与快照。输入=page_plan，输出=可编辑演示文件+备注+证据映射。
- 步骤 4 渲染与修复（540–720s，≤18 请求）：`office-renderer`/`soffice` 导出 PDF、逐页 PNG；`python3 scripts/main.py check` 查契约缺口，`font-discovery` 查字体/溢出；改动前 `record --kind version` 存快照。输入=成稿，输出=PDF/PNG 预览+修复记录。
- 步骤 5 终检与交付（720–900s，≤12 请求）：`python3 scripts/main.py check` 修复缺口；`main.py finalize --status complete|partial` 写终版；仅当契约字段齐、产物齐、证据齐时标 complete，否则 partial 并写明局限。

单页增量纪律：一次只处理一页或一个校验点。每页按固定结构填写——角色 / 主张（一句）/ 事实绑定（fact_ids）/ 图引用 / 讲稿备注 / 数据出处（locator）。数字一律从 facts.json 引用，不在页面上重打。

## 5. 自检清单（终检前逐条勾选）
实际效果（40）：
- [ ] 可编辑演示文件存在可打开，页数/页序符合约束，每页有叙事角色，备注齐备
- [ ] PDF/PNG 预览真实生成（可查看/可播放）
- [ ] 页上数字、图注、引用编号与原文一致，无编造、无按印象取整
- [ ] 覆盖主线、页结构、图引用、讲解逻辑、模板约束、导出渲染六要素
- [ ] evidence_map、sources、版本快照、change_log、self_check 齐全可复核
使用增益（25）：
- [ ] 记录返工页数、重排次数、导出复查轮次
- [ ] 改进以 diff/日志/公平对照支撑，不以文字自述代替
创新增益（15）：
- [ ] 页序规划、模板槽位填充、图表数据绑定、局部编辑有明确方法与边界
可靠体验（10）：
- [ ] 综述/实验/理论三类与不同模板可复跑、不塌版
- [ ] 缺图、位图无数据、槽位不足、字体缺失、超页、导出失败均有识别与处置
生态贡献（10）：
- [ ] 说明、依赖、参数、页数限制、已知问题齐全
- [ ] 脚本/骨架/检查清单可复用
运行 `python3 scripts/selftest.py` 验证脚本自身可跑（退出码 0）。

## 6. 降级策略（缺数据/缺软件必须如实标注，禁止编造）
- 缺论文 PDF/正文：只用可确证材料，其余页标 `limitation`，不补造段落。
- 图缺失或纯位图无法取数：只引用原图并给准确定位（图号/页码），绝不重绘假曲线、不补数值。
- 模板槽位不足：映射到最近似版式，并在 uncertainty 记录偏差。
- 字体缺失：用 `font-discovery` 选最接近替换并记录，不宣称字体一致。
- 页面溢出：优先删要点，字号不低于 16pt；仍不行则拆页并记录。
- 导出失败：交付可编辑源文件+已成功部分预览，status=partial，写明失败原因与已尝试命令。
- 缺 python-pptx/soffice 等工具：回退可编辑 Markdown/HTML 并说明，绝不伪造“渲染成功”。
- 任何不确定事实：记入 uncertainty 或 pending_human，不进入页面主张。

## 7. 脚本接口（相对 Skill 根目录）
```bash
python3 scripts/main.py scaffold          # 落骨架（幂等，除非 --force）
python3 scripts/main.py record --kind fact --id F1 --quote "逐字" --loc "p.3"
python3 scripts/main.py record --kind evidence --page 4 --role result --claim "..." --fact-ids F1 --refs "Fig 3" --loc "p.7"
python3 scripts/main.py record --kind version --name v1 --note "..." --files /app/output/deck.pptx
python3 scripts/main.py check             # 写 self_check.json，退出码 1=有缺口
python3 scripts/main.py finalize --status complete|partial
python3 scripts/selftest.py               # 空输入/坏 JSON/缺口检出自检
```
脚本只依赖 Python 标准库；目录可用 `INPUT_DIR`/`WORK_DIR`/`OUTPUT_DIR` 环境变量覆盖（默认 /input、/app/work、/app/output）。详细清单见 `references/checklist.md`，方法说明见 `references/method.md`。
默认自查线：页数上限以 case 的 `slide_count` 为准；无约束时每页要点 ≤4 条、正文字号 ≥16pt、单页中文正文 ≤60 字，超出即拆分或删要点并记录。

## 8. 最小调用示例（示意，非正式案例结果）
输入 `/input/case.json`：
```json
{"case_id": "demo-1", "output_contract": {"presentation_file": null, "preview_files": null, "page_plan": null, "evidence_map": null}}
```
调用与示意输出：
```bash
python3 scripts/main.py scaffold          # => {"case_id":"demo-1","contract_fields":4,"warnings":[]}
python3 scripts/main.py record --kind fact --id F1 --quote "..." --loc "p.3"
python3 scripts/main.py check             # => {"pass":N,"fail":0,"warn":M}
python3 scripts/main.py finalize --status partial
```
最终 `/app/output/result.json` 顶层含 `case_id/status/sources/uncertainty/pending_human/change_log` 及全部契约字段。此示例仅说明接口，不冒充正式案例结果。

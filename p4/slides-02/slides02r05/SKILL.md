---
name: academic-slide-contract
description: 把论文或研究材料组织成结构清楚、证据可追溯、可编辑且符合模板的学术汇报演示稿，并以“输出契约优先”逐字段对齐 result.json。适用：案例要求“学术 PPT 生成与编辑”，需从固定论文/材料产出可编辑幻灯片、讲稿、页级证据映射、渲染件与版本/变更记录时使用。
version: 1.1.0
---

# 学术汇报演示稿构建（契约优先版）

## 1. 角色与何时使用
本 Skill 把固定论文或研究材料组织为可编辑、可复核、符合母版/版式的学术演示稿，并把每个页级主张、图表引用、数字出处落到证据映射与变更记录。
适用：案例要求“学术 PPT 生成与编辑”，需产出可编辑演示文件、讲稿/备注、页级证据映射、渲染预览与版本记录。
不适用：论文精读综述、科研绘图算法、文字润色、PPT 软件二次开发；这些只做必要支撑，不展开为交付主体。
核心差异点：本版以“输出契约完整”为第一优先级——result.json 的字段不写死，而是从 case.json 的 output_contract 发现后逐字段对齐，缺失值显式给 null/[]/{}，未知键不删除，change_log 作为一等数据结构全程记录。

## 2. 输入契约（运行时）
- 原始输入：`/input/case.json` 与 `/input/task.md`；论文 PDF、图、数据表、母版 .pptx 等材料位于 `/input/` 或其子目录，以实际文件为准。
- 工作区 `/app/work/`；最终产物写 `/app/output/`（必须含 result.json）。
- 读取优先级：以 case.json/task.md 与实际文件内容为准，不用文件名或领域标签臆断；PDF 必须用 pdf-text-tool/pdftotext 实际提取，扫描页用 ocr-tool。
- 关键输入：`case_id`、`output_contract`（题目要求的 result.json 字段集合）、`audience/time/slide_count/template/language` 等约束；缺失时按材料事实推断，并写入 uncertainty。

## 3. 输出契约（契约优先，核心）
必须产出 `/app/output/result.json`，按“发现→对齐→校验”三步保证完整：
1. **发现**：从 case.json 读取 `output_contract`（兼容 `outputContract`/`contract`，可为字段名数组或 {字段:类型} 对象）；缺失时用内置默认契约：presentation_file、preview_files、speaker_notes、page_plan、evidence_map、version_records。
2. **对齐**：契约声明字段原样保留，缺值给 null 或空结构，绝不编造、绝不改名、绝不删除未知键。
3. **校验**：终检时逐字段核对“是否出现、是否为空”，写 self_check.json；有缺口则 status 不得为 complete。

result.json 顶层固定包含：
- `case_id`（字符串，取自 case.json）；
- 契约声明全部字段（字段名原样保留）；
- 保留字段：`status`（partial/complete/failed/insufficient_evidence/input_error）、`sources`、`uncertainty`、`pending_human`、`change_log`、`errors`、`degradations`、`runtime`。
change_log 是每步必写的一等结构：每条含 `{t, seq, step, action, status, reason}`，按 seq 递增；脚手架、成稿、渲染、终检四阶段至少各一条。

可编辑 Office/幻灯片/示意图必须同时给出真实渲染的 PDF/PNG 预览；部分结果必须标 limitations。

## 4. 执行流程（单案例，总预算 900s / 64 次模型请求）
硬规则：**前 3 分钟（180s）内必须落盘一版 `/app/output/result.json` 骨架**（case_id + 全部契约字段 + status=partial），此后只做增量改进；除字段损坏外不得清空重来（脚本自动增量合并，不覆盖已有值）。

- 步骤 0 落骨架（0–60s，≤2 请求）：读 case.json/task.md，运行 `python3 scripts/main.py scaffold`；产出 result.json、work/case_summary.json，并种子 change_log。
- 步骤 1 盘点材料（60–180s，≤4 请求）：列出 /input 下全部文件；pdftotext 提正文，pdf-render-tool 渲染图页，OCR 处理扫描图。输出=正文文本、图页 PNG、带编号图注清单。
- 步骤 2 主线与页计划（180–300s，≤8 请求）：抽取研究问题/方法/结果/限制；定页序与每页叙事角色；用 `record field --key page_plan ...` 写 page_plan（每页：角色、主张、图/表引用、数据出处、字数预算）。
- 步骤 3 成稿（300–540s，≤20 请求）：有母版 .pptx 必须编辑模板并保留母版/版式，不得新建覆盖；逐页填主张+图引用+讲稿备注；数字/图注/引用编号逐字核对原文；用 `record source/evidence` 写 sources 与 evidence_map。
- 步骤 4 渲染与修复（540–720s，≤18 请求）：office-renderer/soffice 导出 PDF、逐页 PNG；`main.py check` 查空页/超长页/占位文本；font-discovery 查字体与溢出；改动前 `record version` 存快照。
- 步骤 5 终检与交付（720–900s，≤12 请求）：`main.py check` 修复缺口；`main.py finalize --status ...` 写终版；契约字段齐、产物齐、证据齐才标 complete，否则 partial 并写明局限。
单响应 16384 token 上限下，一次只处理一页或一个校验点，优先保证骨架与关键数字正确。

## 5. 自检清单（逐条勾选，对应评分维度）
实际效果（40）：
- [ ] 可编辑演示文件存在且可打开；页数/页序符合约束；每页有叙事角色；备注齐备
- [ ] PDF/PNG 预览真实生成（可播放/可查看）
- [ ] 页上数字、图注、引用编号与原文/数据表一致，无编造、无按印象取整
- [ ] 覆盖主线、页结构、图引用、讲解逻辑、模板约束、导出渲染六要素
- [ ] evidence_map、sources、版本快照、change_log、self_check 齐全，可复跑复核
使用增益（25）：
- [ ] 同输入同模板可比；记录返工页数/重排次数/复查轮次作为效率证据
- [ ] 质量/效率改进以 diff、运行日志、公平对照等可核验记录支撑，不以文字自述代替
创新增益（15）：
- [ ] 页序规划、模板槽位填充、图表数据绑定、局部编辑有明确方法与边界说明
- [ ] 输出契约字段逐一对齐 output_contract，未知键保留、缺失值显式空值，可被审计
可靠体验（10）：
- [ ] 按本文能稳定触发；综述/实验/理论三类与不同模板可复跑、不塌版
- [ ] 缺图、位图无数据、槽位不足、字体缺失、超页、导出失败均有识别与处置
生态贡献（10）：
- [ ] 使用说明、依赖、参数、页数限制、已知问题齐全；脚本/骨架/检查清单可复用

## 6. 降级策略（缺数据/缺软件必须如实标注，禁止编造）
- 缺论文 PDF/正文：只用可确证材料，其余页标 `limitation`，不补造段落。
- 图缺失或纯位图无法取数：只引用原图并给准确定位（图号/页码），绝不重绘假曲线、不补数值。
- 模板槽位不足：映射到最近似版式，并在 uncertainty 记录偏差。
- 字体缺失：用 font-discovery 选最接近替换并记录，不宣称字体一致。
- 页面溢出：优先删要点，字号不低于 16pt；仍不行则拆页并记录。
- 导出失败：交付可编辑源文件+已成功部分预览，status=partial，写明失败原因与已尝试命令。
- 缺 python-pptx/soffice 等工具：回退到可编辑 Markdown/HTML 并说明，绝不伪造“渲染成功”。
- 任何不确定事实：记入 uncertainty，不进入页面主张。

## 7. 脚本调用（相对 Skill 根目录，运行时为 /skills/weekly-skill/）
```bash
python3 scripts/main.py scaffold
python3 scripts/main.py record change --step build --action "..." --reason "..."
python3 scripts/main.py record source --ref "Fig 3" --loc "论文 p.7" --kind figure
python3 scripts/main.py record evidence --page 4 --role result --claim "..." --refs "Fig 3" --loc "p.7" --quote "..."
python3 scripts/main.py record version --name v1 --note "..." --files /app/output/deck.pptx
python3 scripts/main.py record field --key presentation_file --value /app/output/deck.pptx
python3 scripts/main.py check --strict
python3 scripts/main.py finalize --status complete|partial|failed
python3 scripts/selftest.py
```
退出码约定：check 有缺口→1；finalize 标 complete→0、标 partial/failed→1；脚本不依赖当前工作目录，目录可用 `INPUT_DIR`/`WORK_DIR`/`OUTPUT_DIR` 覆盖（默认 /input、/app/work、/app/output）。

## 8. 边界
- 本 Skill 不实现科研绘图算法与 PPT 软件本身；图表重绘仅在数据可复核时进行。
- 脚本只依赖 Python 标准库；PPTX 生成由运行时已装工具（python-pptx/office-renderer）完成，脚本负责契约骨架、增量记录与终检。
- 正式案例、白板与评分由平台提供；本包不含预写答案或隐藏测试。

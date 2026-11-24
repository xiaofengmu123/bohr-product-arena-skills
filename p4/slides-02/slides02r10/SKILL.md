---
name: deck-spec-builder
description: 把论文或研究材料组织为可编辑、可复核、符合母版约束的学术演示稿：先产出可校验的页级内容规格（deck spec），再据此渲染可编辑 .pptx，最后按规则校验导出件并在 /app/output 交付带证据映射与版本记录的 result.json。当任务要求“学术 PPT 生成与编辑”时使用。
version: 1.1.0
whenToUse: 案例要求“学术 PPT 生成与编辑”，需从固定论文/材料产出可编辑幻灯片、页级主张、图表引用、讲稿备注、模板约束与真实渲染预览时使用。
---

# 学术汇报演示稿构建（deck-spec-builder）

## 1. 角色与何时使用
把固定论文或研究材料“翻译”成结构清楚、证据可追溯、可编辑、符合母版的演示稿。核心不是复制段落，而是取舍与重排：确定每页叙事角色、页级主张、图/表引用、讲解顺序、讲稿备注，并把每个关键结论绑定回材料原文。
适用：页级主张、图/表引用、讲稿备注、母版合规、导出渲染、可复核交付。
不适用：论文精读综述、科研绘图算法、文字润色、PPT 软件二次开发——这些只做必要支撑，不作为交付主体。

## 2. 输入契约（运行时）
- 原始输入：`/input/case.json` 与 `/input/task.md`；论文 PDF、图、数据表、母版 .pptx、起始稿等位于 `/input/` 或其子目录，以实际文件为准。
- 工作区 `/app/work/`，最终产物与 `result.json` 写 `/app/output/`。
- 读取优先级：以 case.json 的 `output_contract`、task.md 与实际文件内容为准；不用文件名或领域标签臆断。PDF 必须用 pdf-text-tool/pdftotext 实际提取，扫描页用 ocr-tool；渲染图页用 pdf-render-tool/pdftoppm。
- 关键输入：`case_id`、`output_contract`（题目要求的 result.json 字段集合）、audience/time/slide_count/template/language 等约束；缺失时按材料事实推断并写入 `uncertainty`。

## 3. 输出契约（必须产出 /app/output/result.json）
JSON 顶层必须含：
1. `case_id`（字符串，取自 case.json）；
2. `output_contract` 声明的**全部字段**（字段名原样保留；值为字符串/对象/数组；未完成项用 `null`/`[]`，绝不编造或删未知键）；
3. 保留字段：`status`（`partial|complete|failed`）、`sources`（证据定位数组）、`uncertainty`、`pending_human`、`change_log`（按时间排序，含 time/step/action/status/reason）。
常见交付字段：`presentation_file`、`preview_files`、`speaker_notes`、`page_plan`、`evidence_map`、`version_records`（以实际 `output_contract` 为准）。
可编辑 Office/幻灯片/示意图必须同时给真实渲染的 PDF/PNG 预览；partial 结果必须写明局限。
本包采用 **规格先行（spec-first）** 中间表示：`/app/work/deck_spec.json` 是内容事实源，渲染与校验都消费它；渲染前先通过规格校验，渲染后再做产物校验。

## 4. 执行流程（单案例：900s / 64 次模型请求 / 单响应 16384 token）
硬规则：**前 3 分钟（180s）内必须落盘一版 `/app/output/result.json` 骨架**（case_id + 全部契约字段 + status=partial），此后只做增量改进，除字段损坏外不得清空重来。

- 步骤 0 落骨架（0–60s，≤2 请求）：读 case.json/task.md，运行 `python3 scripts/main.py scaffold`，产出 result.json、work/case_summary.json、change_log 种子。输入=case.json，输出=骨架。
- 步骤 1 盘点材料（60–180s，≤4 请求）：`python3 scripts/main.py inventory` 列出 /input 全部文件；pdf-text-tool 提正文，pdf-render-tool 渲染图页，OCR 处理扫描页。输出=正文文本、图页 PNG、带编号图注清单。
- 步骤 2 主线与页计划（180–300s，≤8 请求）：抽取研究问题、方法、结果、限制；定页序与每页叙事角色；写 `deck_spec.json`（每页：role、claim、bullets、figure/table 引用、locator+quote、notes、字数预算）；`python3 scripts/main.py spec` 校验结构。
- 步骤 3 成稿（300–540s，≤20 请求）：若有母版 .pptx，必须编辑模板并保留母版/版式，不得新建覆盖；逐页填主张+图引用+讲稿备注；数字/图注/引用编号逐字核对原文；`python3 scripts/main.py bind` 把每页 locator+quote 写回 evidence_map。
- 步骤 4 渲染与修复（540–720s，≤18 请求）：office-renderer/soffice 导出 PDF、逐页 PNG；`python3 scripts/main.py pptx-info <file>` 读回文本与占位符，`check` 查空页/超长页/占位文本/母版缺失；font-discovery 查字体、查溢出；改动前 `python3 scripts/main.py version` 存快照。
- 步骤 5 终检与交付（720–900s，≤12 请求）：`python3 scripts/main.py check` 修复缺口；`finalize` 写终版；仅当契约字段齐、产物齐、证据齐时标 complete，否则 partial 并写明局限。
一次只处理一页或一个校验点，避免整篇长文一次塞入；优先保证骨架与关键数字正确。

## 5. 自检清单（逐条勾选，对应评分维度）
- [ ] 实际效果：可编辑演示文件存在且可打开；页数/页序符合约束；每页有叙事角色；讲稿备注齐备；PDF/PNG 预览真实生成。
- [ ] 实际效果：页上数字、图注、引用编号与原文/数据表一致，无编造、无按印象取整。
- [ ] 实际效果：覆盖主线、页结构、图引用、讲解逻辑、模板约束、导出渲染六要素。
- [ ] 实际效果：evidence_map、sources、版本快照、change_log 齐全，每项证据含 locator+逐字 quote，第三方可独立复核复跑。
- [ ] 使用增益：与基础 Agent 同输入同模板可比；记录返工页数、重排次数、导出复查轮次作为效率证据。
- [ ] 使用增益：质量/效率改进以 diff、运行日志、公平对照等可核验记录支撑，不以文字自述代替。
- [ ] 创新增益：deck spec 先行、母版槽位填充、图表数据绑定、局部编辑有明确方法与边界说明。
- [ ] 可靠体验：按本文能稳定触发；综述/实验/理论三类与不同模板可复跑、不塌版。
- [ ] 可靠体验：缺图、位图无数据、槽位不足、字体缺失、超页、导出失败均有识别与处置。
- [ ] 生态贡献：使用说明、依赖、参数、页数限制、已知问题齐全；脚本/骨架/检查清单可复用。

## 6. 降级策略（缺数据/缺软件如实标注，禁止编造）
- 缺论文 PDF/正文：只用可确证材料，其余页标 `limitation`，不补造段落。
- 图缺失或纯位图无法取数：只引用原图并给准确定位（图号/页码），绝不重绘假曲线、不补数值。
- 模板槽位不足：映射到最近似版式，并在 uncertainty 记录偏差。
- 字体缺失：用 font-discovery 选最接近替换并记录，不宣称字体一致。
- 页面溢出：优先删要点，字号不低于 16pt；仍不行则拆页并记录。
- 导出失败：交付可编辑源文件+已成功部分预览，status=partial，写明失败原因与已尝试命令。
- 缺 python-pptx/soffice 等工具：回退到可编辑 Markdown/HTML 并说明，绝不伪造“渲染成功”。
- 任何不确定事实：记入 uncertainty，不进入页面主张。

## 7. 脚本与自检
脚本只依赖 Python 标准库，路径相对本 Skill 根目录（运行时为 `/skills/weekly-skill/`），不依赖当前工作目录；`INPUT_DIR`/`WORK_DIR`/`OUTPUT_DIR` 环境变量可覆盖默认 `/input`、`/app/work`、`/app/output`。
```bash
python3 scripts/main.py scaffold            # 落骨架（前 3 分钟）
python3 scripts/main.py inventory           # 盘点材料
python3 scripts/main.py spec                # 校验 deck_spec.json
python3 scripts/main.py bind                # 绑定证据到 evidence_map
python3 scripts/main.py pptx-info <file>    # 读回文本/占位符/母版
python3 scripts/main.py check               # 写 self_check.json，退出码 1=有缺口
python3 scripts/main.py finalize --status complete|partial
python3 -B scripts/selftest.py              # 离线自检，退出码 0=通过
```
详细契约与校验规则见 `references/contract.md`，校验规则表见 `references/checks.md`。

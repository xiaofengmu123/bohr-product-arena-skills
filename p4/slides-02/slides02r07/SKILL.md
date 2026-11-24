---
name: academic-slide-builder-robust
description: 把论文或研究材料组织成结构清晰、证据可追溯、可编辑且符合模板的学术汇报演示稿，含页级主张、图表引用、讲稿备注、母版合规与导出渲染校验；以鲁棒性为先，空输入、坏 JSON、坏编码、缺参数、超大文件均可优雅降级并在 /app/output 交付可复核的 result.json 与产物。
version: 1.0.0
whenToUse: 案例要求“学术 PPT 生成与编辑”，需从固定论文或材料产出可编辑幻灯片、讲稿、页级证据映射与渲染件，或需在异常输入下仍给出诚实、可复核结果时使用。
---

# 学术汇报演示稿构建（鲁棒变体）

## 1. 角色与何时使用
本 Skill 指导 Agent 把固定论文或研究材料组织为结构清楚、证据可追溯、可编辑且符合模板的学术演示稿，并交付可复核产物与 result.json。
适用：案例要求“学术 PPT 生成与编辑”，需产出页级主张、图表引用、讲稿/备注、可编辑演示文件与渲染件；或需在输入异常时仍诚实降级。
不适用：论文精读综述、科研绘图算法本身、文字润色、PPT 软件二次开发；这些只做必要支撑，不展开为交付主体。

## 2. 输入契约（运行时）
- 原始输入：`/input/case.json` 与 `/input/task.md`；论文 PDF、图、数据表、母版 .pptx 等材料位于 `/input/` 或其子目录，以实际文件为准。
- 工作区 `/app/work/`；最终产物写 `/app/output/`（含 `result.json`）。
- 读取优先级：以 case.json/task.md 与实际文件内容为准，不用文件名或领域标签臆断；PDF 必须用 pdf-text-tool/pdftotext 实际提取，扫描页用 ocr-tool。
- 关键输入：`case_id`、`output_contract`（题目要求的 result.json 字段集合）、`audience/time/slide_count/template/language` 等约束；缺失时按材料事实推断并写入 `uncertainty`，绝不编造。
- 鲁棒性硬约束：文本按 utf-8-sig 读入并容错；坏 JSON/坏编码降级不崩溃；单文件超上限（文本 5MB、二进制 50MB）跳过并记入 `degradations`。

## 3. 输出契约
必须产出 `/app/output/result.json`，顶层包含：
1. `case_id`（字符串，取自 case.json）；
2. `output_contract` 声明的全部字段（字段名原样保留；未完成项为 null 或空结构，绝不编造）；
3. 保留字段：`status`（complete/partial/insufficient_evidence/input_error/failed）、`sources`、`uncertainty`、`pending_human`、`change_log`（时间有序）、`errors`、`degradations`、`runtime`。
常见交付字段：`presentation_file`、`preview_files`、`speaker_notes`、`page_plan`、`evidence_map`、`version_records`（以实际 output_contract 为准）。
可编辑 Office/幻灯片/示意图必须同时给出真实渲染的 PDF/PNG 预览；partial 结果必须写明局限。
骨架由 `scripts/main.py` 生成，增量记录用 `scripts/record.py`，终检/自检用 `scripts/selftest.py`；三者只依赖 Python 标准库，脚本路径相对 Skill 根目录（运行时 `/skills/weekly-skill/`），不依赖当前工作目录，输入输出由 `--input/--output/--work` 决定。

## 4. 执行流程（总预算 900s / 64 次模型请求）
硬规则：**前 3 分钟（180s）内必须落盘一版 `/app/output/result.json` 骨架**（case_id + 全部契约字段 + status=partial），此后只做增量改进；除字段损坏外不得清空重来。单响应 ≤16384 token，一次只处理一页或一个校验点。
- 步骤 0 落骨架（0–60s，≤2 请求）：读 case.json/task.md，运行 `python3 scripts/main.py --input /input --output /app/output --work /app/work`；产出 result.json(partial)、`work/case_summary.json`、`work/input_manifest.json`、change_log 种子。输入=case.json，输出=骨架。
- 步骤 1 盘点材料（60–180s，≤4 请求）：按 input_manifest 列文件；pdf-text-tool/pdftotext 提正文，pdf-render-tool 渲染图页，OCR 处理扫描图。输出=正文文本、图页 PNG、带编号图注清单。
- 步骤 2 主线与页计划（180–300s，≤8 请求）：抽取研究问题、方法、结果、限制；定页序与每页叙事角色；写 `page_plan`（每页：角色、主张、图/表引用、数据出处、字数预算）。
- 步骤 3 成稿（300–540s，≤20 请求）：若提供母版 .pptx，必须编辑模板并保留母版/版式，不得新建覆盖；逐页填主张+图引用+讲稿备注；数字/图注/引用编号逐字核对原文；`record.py` 写 evidence_map 与 sources。
- 步骤 4 渲染与修复（540–720s，≤18 请求）：office-renderer/soffice 导出 PDF、逐页 PNG；`record.py version` 存快照；查空页/超长页/占位文本/字体，改动前存快照。
- 步骤 5 终检与交付（720–900s，≤12 请求）：`python3 scripts/selftest.py --check --output /app/output`；`record.py set-status complete|partial`；补齐 uncertainty/pending_human；仅当契约字段齐、产物齐、证据齐时标 complete。

## 5. 鲁棒性设计（本版侧重）
在保持完整性的前提下，把“空输入、坏 JSON、坏编码、缺参数、超大文件、写盘失败”作为一等场景，与参考包形成明显不同的设计取舍：
- 空输入目录：退出码 0，status=insufficient_evidence，列出 missing_artifacts，不抛异常、不编造。
- 坏 JSON/坏编码：依次尝试 utf-8-sig、utf-8、gb18030、latin-1；仍失败则 status=input_error 并记录 errors，继续产出合法骨架。
- 缺参数：`--input/--output/--work` 缺省 `/input`、`/app/output`、`/app/work`。
- 超大文件：文本 >5MB、二进制 >50MB 跳过并写 degradations，避免读入内存崩溃；扫描总量超 500MB 停止并记录。
- 原子写盘：先写 `.tmp` 再 `os.replace`；`/app/output` 写失败回退 `/app/work`，再失败才以清晰消息退出（不抛堆栈）。
- 幂等与确定性：`sort_keys=True`、固定遍历顺序，同输入重复运行骨架一致（时间戳除外）。
- result.json 缺失/损坏：`record.py` 先重建最小骨架再记录，不中断后续增量。

## 6. 自检清单（逐条勾选，对应评分维度）
- [ ] 实际效果：可编辑演示文件存在且可打开；页数/页序符合约束；每页有叙事角色；讲稿备注齐备；PDF/PNG 预览真实生成。
- [ ] 实际效果：页上数字、图注、引用编号与原文/数据表一致，无编造、无按印象取整。
- [ ] 实际效果：覆盖主线、页结构、图引用、讲解逻辑、模板约束、导出渲染六要素。
- [ ] 实际效果：evidence_map、sources、版本快照、change_log 齐全，第三方可独立复核复跑。
- [ ] 使用增益：同输入同模板可比；记录返工页数、重排次数、导出复查轮次作为效率证据。
- [ ] 使用增益：质量/效率改进以 diff、运行日志、公平对照等可核验记录支撑，不以文字自述代替。
- [ ] 创新增益：页序规划、模板槽位填充、图表数据绑定、局部编辑有明确方法与边界说明。
- [ ] 可靠体验：按本文稳定触发；综述/实验/理论三类与不同模板可复跑、不塌版。
- [ ] 可靠体验：缺图、位图无数据、槽位不足、字体缺失、超页、导出失败均有识别与处置。
- [ ] 鲁棒性：空输入/坏 JSON/坏编码/缺参数/超大文件/写盘失败均优雅降级（`selftest.py` 逐项验证）。
- [ ] 生态贡献：使用说明、依赖、参数、页数限制、已知问题齐全；脚本/骨架/检查清单可复用。

## 7. 降级策略（缺数据/缺软件必须如实标注，禁止编造）
| 情形 | 处置 | 结果标注 |
|-|-|-|
| 论文 PDF/正文缺失 | 只用可确证材料，其余页标 limitation | uncertainty |
| 图缺失/纯位图无数据 | 引用原图+准确定位（图号/页码），不重绘不补值 | uncertainty |
| 模板槽位不足 | 映射最近似版式 | uncertainty |
| 字体缺失 | font-discovery 选最接近替换 | uncertainty |
| 页面溢出 | 先删要点，字号≥16pt；仍不行拆页 | change_log |
| 导出失败 | 交付可编辑源文件+已成功预览 | status=partial + 失败原因 |
| 缺 python-pptx/soffice | 回退可编辑 Markdown/HTML，不伪造渲染 | status=partial |
| 空输入/坏 JSON/坏编码/超大文件 | 按第 5 节降级，仍写合法 result.json | status/errors/degradations |
| 数字/事实不确定 | 不进页面主张 | uncertainty |

## 8. 脚本接口（相对 Skill 根目录，运行时 `/skills/weekly-skill/`）
```bash
python3 scripts/main.py --input /input --output /app/output --work /app/work
python3 scripts/record.py --output /app/output change --step build --action "..." --reason "..."
python3 scripts/record.py --output /app/output source --ref "Fig 3" --loc "论文 p.7" --kind figure
python3 scripts/record.py --output /app/output evidence --page 4 --role result --claim "..." --refs "Fig 3" --loc "p.7"
python3 scripts/record.py --output /app/output version --name v1 --note "..." --files /app/output/deck.pptx
python3 scripts/record.py --output /app/output set-field --field presentation_file --value deck.pptx
python3 scripts/record.py --output /app/output set-status --status complete
python3 scripts/selftest.py
python3 scripts/selftest.py --check --output /app/output
```
说明：`record.py` 每次调用自动追加 change_log；保留字段不可被 `set-field` 覆盖；`--used-in`/`--refs`/`--files` 用逗号分隔。

## 9. 已知边界
- 本 Skill 不实现科研绘图算法与 PPT 软件本身；图表重绘仅在数据可复核时进行。
- 脚本只依赖 Python 标准库；PPTX 生成由运行时已装工具（python-pptx/office-renderer）完成，脚本负责骨架、记录与终检。
- 正式案例、白板与评分由平台提供；本包不含预写答案或隐藏测试。

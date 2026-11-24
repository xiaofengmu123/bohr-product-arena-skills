---
name: academic-slide-builder-deterministic
description: 把论文或研究材料组织成结构清楚、证据可追溯、可编辑且符合母版的学术汇报演示稿：页级主张、图表引用、讲稿备注、母版合规、导出渲染校验与逐字证据溯源；用确定性序列化保证同一输入两次运行 result.json 字节一致。
version: 1.0.0
whenToUse: 案例要求“学术 PPT 生成与编辑”，需从固定论文/材料产出可编辑幻灯片、讲稿、页级证据映射与真实渲染件，且要求结果可复核、可复跑时使用。
---

# 学术汇报演示稿构建（确定性版）

## 1. 角色与何时使用
本 Skill 把固定论文或研究材料组织为结构清楚、证据可追溯、可编辑且符合母版的学术演示稿，并交付可复核产物：页级主张、图表引用、讲稿/备注、可编辑 .pptx（或等价可编辑件）与真实 PDF/PNG 预览。
适用：案例要求“学术 PPT 生成与编辑”，需要输出主线、页结构、图表引用、讲解逻辑、模板约束、导出渲染六类要素。
不适用：论文精读综述、科研绘图算法、文字润色、PPT 软件二次开发——这些只做必要支撑，不作为交付主体。

## 2. 输入契约（运行时）
- 原始输入：`/input/case.json` 与 `/input/task.md`；论文 PDF、图、数据表、母版 .pptx 等位于 `/input/` 或其子目录，以实际文件为准。
- 工作区 `/app/work/`；最终产物写 `/app/output/`。
- 读取优先级：以 case.json/task.md 与实际文件内容为准，不用文件名或领域标签臆断；PDF 必须用 pdf-text-tool/pdftotext 实际提取，扫描页用 ocr-tool。
- 关键输入：`case_id`、`output_contract`（result.json 字段集合）、`audience/time/slide_count/template/language` 等约束；缺失时按材料事实推断，并在 `uncertainty` 标注。

## 3. 输出契约（运行时）
必须产出 `/app/output/result.json`，顶层包含：
1. `case_id`（字符串，取自 case.json 的 id/case_id/armiId）；
2. `output_contract` 声明的全部字段（字段名原样保留；未完成项用 null 或空结构，绝不编造）；
3. 标准字段：`status`（complete/partial/failed）、`presentation_file`、`preview_files`、`speaker_notes`、`page_plan`、`evidence_map`、`version_records`、`sources`、`uncertainty`、`pending_human`、`change_log`、`determinism`、`errors`、`degradations`。
可编辑演示/示意图必须同时给出真实渲染的 PDF/PNG 预览；partial 必须写明局限。
- `sources` 每项：`{ref, loc, kind, quote, used_in}`，逐字 quote。
- `evidence_map` 每项：`{page, role, claim, refs, loc, quote}`，quote 必填。
- `change_log` 每项：`{seq, step, budget, action, reason}`，按 seq 升序，无墙钟时间（见第 8 节确定性）。

## 4. 执行流程（单案例，预算 900s / 64 请求 / 16384 token）
硬规则：**前 3 分钟（180s）内必须落盘 `/app/output/result.json` 骨架**（case_id + 全部契约字段 + status=partial），此后只增量改进；除字段损坏外不得清空重来。

| 步骤 | 时间 | 请求上限 | 输入 | 输出 |
|-|-|-|-|-|
| 0 落骨架 | 0–60s | ≤2 | case.json/task.md | result.json 骨架、work/case_summary.json、change_log 种子 |
| 1 盘点材料 | 60–180s | ≤4 | /input 全文件 | 正文文本、图页 PNG、带编号图注清单 |
| 2 主线与页计划 | 180–300s | ≤8 | 正文/图注 | page_plan（每页：角色、主张、图/表引用、出处、字数预算） |
| 3 成稿 | 300–540s | ≤20 | 母版+计划 | 逐页主张+图引用+讲稿备注，数字/图注/编号逐字核对 |
| 4 渲染与修复 | 540–720s | ≤18 | 可编辑稿 | PDF/PNG 预览，finalize 查空页/超长/占位，font-discovery 查字体/溢出 |
| 5 终检与交付 | 720–900s | ≤12 | 全部产物 | finalize 写终版；契约齐/产物齐/证据齐才 complete，否则 partial |

单响应 16384 token 上限下，一次只处理一页或一个校验点；优先保证骨架与关键数字正确。

## 5. 脚本调用（相对本 Skill 根目录）
```bash
python3 scripts/main.py scaffold          # 前 3 分钟落骨架（读 INPUT_DIR/... 环境变量）
python3 scripts/main.py record change --step build --action "..." --reason "..."
python3 scripts/main.py record source --ref "Fig 3" --loc "论文 p.7" --kind figure --quote "..."
python3 scripts/main.py record evidence --page 4 --role result --claim "..." --refs "Fig 3" --loc "p.7" --quote "..."
python3 scripts/main.py record plan --page 3 --role result --claim "..." --refs "Fig 3" --loc "p.7"
python3 scripts/main.py record version --name v1 --note "..." --files deck.pptx
python3 scripts/main.py record artifact --presentation deck.pptx --preview preview/1.png
python3 scripts/main.py record field --key slide_count --value 6
python3 scripts/main.py record uncertainty --missing "图缺失" --note "原因"
python3 scripts/main.py record pending --item "需人工确认 Fig 2 数据"
python3 scripts/main.py record status --value partial
python3 scripts/main.py check               # 写 self_check.json，退出码 1=有缺口
python3 scripts/main.py finalize --status complete|partial|failed
python3 scripts/selftest.py                 # 交付前自检（确定性+降级）
```
环境变量：`INPUT_DIR`（默认 /input）、`WORK_DIR`（默认 /app/work）、`OUTPUT_DIR`（默认 /app/output）。脚本不依赖当前工作目录。

## 6. 自检清单（交付前逐条勾选；scripts/selftest.py 自动验证确定性与降级）
- [ ] 实际效果：可编辑演示文件存在且可打开；页数/页序符合约束；每页有叙事角色；讲稿备注齐备；PDF/PNG 预览真实生成。
- [ ] 实际效果：页上数字、图注、引用编号与原文/数据表逐字一致，无编造、无按印象取整。
- [ ] 实际效果：覆盖主线、页结构、图引用、讲解逻辑、模板约束、导出渲染六要素。
- [ ] 实际效果：evidence_map、sources、版本快照、change_log、self_check.json 齐全，可独立复核复跑。
- [ ] 使用增益：与基础 Agent 同输入同模板可比；记录返工页数、重排次数、导出复查轮次作为效率证据。
- [ ] 使用增益：质量/效率改进以 diff、运行日志、公平对照等可核验记录支撑，不以文字自述代替。
- [ ] 创新增益：页序规划、模板槽位填充、图表数据绑定、局部编辑有明确方法与边界说明。
- [ ] 可靠体验：综述/实验/理论三类与不同模板可复跑、不塌版。
- [ ] 可靠体验：缺图、位图无数据、槽位不足、字体缺失、超页、导出失败均有识别与处置。
- [ ] 生态贡献：使用说明、依赖、参数、页数限制、已知问题齐全；脚本/清单可复用。
- [ ] 确定性：`scripts/selftest.py` 退出码 0；同一输入两次运行 result.json 字节一致（input_digest 相同）。

## 7. 降级策略（缺数据/缺软件必须如实标注，禁止编造）
- 缺论文 PDF/正文：只用可确证材料，其余页标 `limitation`，不补造段落。
- 图缺失或纯位图无法取数：只引用原图并给准确定位（图号/页码），不重绘假曲线、不补数值。
- 模板槽位不足：映射最近似版式并在 uncertainty 记录偏差。
- 字体缺失：用 font-discovery 选最接近替换并记录，不宣称字体一致。
- 页面溢出：优先删要点，字号不低于 16pt；仍不行拆页并记录。
- 导出失败：交付可编辑源文件+已成功部分预览，status=partial，写失败原因与已尝试命令。
- 缺 python-pptx/soffice：回退可编辑 Markdown/HTML 并说明，绝不伪造“渲染成功”。
- 不确定事实：记入 uncertainty/pending_human，不进入页面主张。

## 8. 确定性设计（本版侧重点）
- result.json 不含墙钟时间戳；运行元数据（起止时间、Python 版本）只写 `work/run_meta.json`，明确排除在确定性校验之外。
- 所有列表（sources/evidence_map/page_plan/change_log/preview_files）写盘前稳定排序；JSON 用 `sort_keys=True, ensure_ascii=False` 规范序列化，原子写盘。
- `determinism.input_digest` 记录 /input 目录排序清单的 SHA-256，供评审验证“同输入→同产物”。
- 禁止随机种子；布局与页序由 page_plan 显式决定，图表重绘仅在有可复核数据时进行。

## 9. 边界与已知问题
- 本 Skill 不实现科研绘图算法与 PPT 软件本身；图表重绘仅在数据可复核时进行。
- 脚本只依赖 Python 标准库；.pptx 生成/渲染由运行时已装工具（python-pptx/office-renderer/soffice）完成，脚本负责骨架、记录、确定性序列化与终检。
- 正式案例、白板与评分由平台提供；本包不含预写答案或隐藏测试。

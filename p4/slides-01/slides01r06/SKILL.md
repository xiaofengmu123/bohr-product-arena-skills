---
name: slides-01
description: 论文转学术 PPT（slides-01）Skill。把一篇论文或研究报告组织成主线成立、页级可讲、证据可回溯、可编辑且能导出预览的学术演示：先写结构化中间产物（plan.json 页级计划 + figure_model.json 图表模型 + draft.json 成稿），再确定性生成真实 .pptx 与页数一致的 PDF，并回填带证据映射与不确定性的 result.json。用于需要把固定论文/汇报稿转成可核验学术 PPT 的场合。
version: 1.0.0
---

# 论文转学术 PPT（slides-01）

## 1. 角色与何时使用

把**一篇论文/研究材料**转成**可讲的学术 PPT**，核心不是"段落切块贴进页面"，而是一次页级结构重写：一页一个主张，主张背后挂论文里的图、表、公式或结论，在给定模板里排得下、讲得通、打开能放映、换机能复现。

适用：给定论文 PDF / 汇报稿 +（可选）模板 pptx + 听众/时长/页数约束，要求交付可编辑 pptx + 渲染 PDF + 页级证据映射。
不适用：论文精读结论生成、科研绘图美化、学术写作润色（相邻课题的产物可作为输入）。

## 2. 输入契约

- 权威契约：`/input/case.json` 的 `case_id` 与 `output_contract`（字段名以它为准）；任务说明 `/input/task.md`。
- 材料：论文/汇报稿/大纲、模板 pptx、图表、目标听众与时长/页数约束，均在 `/input` 下，文件名不定，先 `ls -R /input`。
- 工作目录 `/app/work`（放中间产物），最终产物写 `/app/output/`。
- 实际输入是事实依据：PDF 可能无预提取文本，须真实读取，禁止按标签或领域猜测内容。

## 3. 输出契约（写 `/app/output/result.json`）

必须包含：

1. `case_id`（原样回填）；
2. `output_contract` 里**全部字段**：缺值给 `null`/`[]`，不改名、不删未知键；
3. 统一信封：`status`（`ok | partial | insufficient_evidence | input_error`）、`sources`、`uncertainty`、`pending_human`、`errors`、`degradations`、`change_log`（另保留 `changes` 别名）、`runtime`。

主产物（路径相对 `/app/output`）：真实 `presentation.pptx`（含每页备注）+ 页数一致的 `presentation.pdf`；无 pptx/PDF 能力时给真实 SVG+PNG 并写 `exceptions`/`degradations`，`editable_source`/`export_artifact` 指向真实产物并给页数/校验信息。`content_map` 记录每页 → 证据定位映射。**禁止占位空文件、禁止编造运行分数或科学数据。**

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

**纪律：前 3 分钟内必须落盘一版 result 骨架，之后只做增量改进；每 40 秒至少写一次文件；剩 5 分钟必须已全部落盘。**

| 步骤 | 输入 | 输出 | 预算 |
|---|---|---|---|
| 0 起手 | `/input` | `mkdir -p /app/output`；读 case.json 记 case_id + 契约字段 | 1 min |
| 1 落骨架 | case.json | 立刻写 `/app/output/result.json`（status=partial，契约字段填 null/[]） | 与 0 合并，≤3 min |
| 2 提取主线 | 论文/汇报稿 | `/app/work/plan.json`：主线 narrative + 每页 role/claim/purpose/bullet_points/transition/notes | 4 min |
| 3 建图表模型 | 论文图/表/公式 | `/app/work/figure_model.json`：每个图表的 label/kind/caption/locator/quote/used | 3 min |
| 4 成稿 | plan + figure | `/app/work/draft.json`：逐页 title/body/notes（备注不与页面文字重复） | 3 min |
| 5 校验 | 三者 | `python3 -B scripts/main.py --step validate`，修完引用错位与缺 quote | 2 min |
| 6 构建 | draft | `python3 -B scripts/main.py --step build` → pptx+pdf，回填 result.json | 2 min |
| 7 复核 | pptx/pdf | 用导出 PDF 预览页数一致；逐条过 §5 自检；改写 result.json | 剩余 |

脚本只是**可选单次工具**：读材料、抽主线、写主张、写备注由你自己完成，脚本不替你组织内容；读不出就写 `uncertainty`/`pending_human`，绝不编造。

## 5. 自检清单（逐条勾选，对应评分维度）

- [ ] 主线成立：首页问题/背景 → 方法 → 关键结果 → 讨论/限制 → 结论，与论文贡献一致，非段落顺序搬运。
- [ ] 页级主张：每页一个 claim，角色在 `title/background/method/result/discussion/limitation/conclusion` 内，单页信息密度受控。
- [ ] 证据映射：每页 evidence 有 `locator` + **逐字 `quote`**，quote 命中材料原文，未改写数值/单位/方法名。
- [ ] 图表一致：引用的 Fig./Table 编号与 figure_model 对得上，无"讲第三页引用不存在的图"；示意图不冒充实验数据。
- [ ] 模板/版面：遵守模板占位符/字号层级/配色/页脚，长句改写为短语，无溢出；页数不超上限。
- [ ] 讲解备注：每页有 notes，写过渡/强调点/被问时的补充证据，不重复页面文字。
- [ ] 导出渲染：`presentation.pdf` 页数与 pptx 一致；字体/项目符号不崩坏。
- [ ] 可编辑可复跑：pptx 可逐页编辑；同一输入重跑结果一致（确定性布局、固定种子）。
- [ ] 诚实交付：`status` 与缺口一致；缺数据/缺软件记 `limitations`/`degradations`，不编造。

## 6. 降级策略（如实标注 limitations，不编造）

- 缺 `/input` 或 case.json 坏：仍写 result.json，`status="input_error"`、`case_id="unknown-case"`、`errors` 记原因。**没有 result.json 才是 0 分。**
- 图表无法提取 / 公式为图 / PDF 加密：页级 evidence 标 `not_reported` 或写入 `pending_human`，不猜数值。
- 模板占位符不足 / 字体缺失 / 页数超限：写 `exceptions`，必要时缩并页面或降为纯文本版式。
- 无 soffice/LibreOffice 导出 PDF：脚本给 SVG/PNG 回退并在 `export_artifact`/`degradations` 记录；**不得伪造 `presentation.pdf`**。
- 冲突信息：两个来源不一致时都记录并标 `source_conflict`。

## 7. 脚本与参考（相对路径，标准库 only）

```bash
python3 -B scripts/main.py --help                # 查看步骤
python3 -B scripts/main.py --step scaffold       # 只落 result 骨架
python3 -B scripts/main.py --step validate       # 校验中间产物
python3 -B scripts/main.py --step build          # plan/draft → pptx + pdf + 回填 result
python3 -B scripts/selftest.py                   # 全链路自检（合成输入，已标记为示例）
```

- `scripts/pptxlib.py`：纯标准库 OOXML 写入器 + PDF 页数检测 + 尽力而为的 LibreOffice 转换。
- `references/contract.md`：result.json 与三个中间产物的完整字段 schema。
- `references/checklist.md`：自检与降级细则（评审维度逐条对应）。

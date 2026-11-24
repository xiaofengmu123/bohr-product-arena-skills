---
name: talk-evidence-organizer
description: 把论文或研究材料组织为证据可定位、逐字可回溯、可编辑且符合模板的学术汇报（页级唯一主张、图表引用、讲解顺序、讲稿备注、问答准备、渲染检查），并把不确定性与材料缺失显式化，缺数据不编造。用于固定科研案例从已有材料到可上台、可复核演示文件的组织与交付。
version: 1.0.0
---

# 学术汇报证据组织 Skill

## 1. 角色与何时使用

本 Skill 解决“从已有研究材料到可讲页面”的组织问题，核心不是压缩论文，而是：**每一页只承载一条可讲主张，每条主张都能逐字指回源材料，证据强度与表述口径一致，取舍与不确定性显式记录**。

触发条件（任一满足即用）：
- 任务要求把论文/研究材料组织成演示文稿，且需逐页标注主张、图表与证据来源；
- 输入含听众、时长、页数、模板等约束，需要内容取舍与叙事顺序设计；
- 评分要求证据可追溯、结论强度与证据匹配、导出可打开可复核。

不适用：论文精读总结、学术写作成稿、科研绘图素材生产（相邻课题负责）。

核心方法：先定报告目标与听众 → 定“问题—方法—结果—结论”主线与页级取舍 → 每页唯一主张 + 支撑证据 + 证据强度 → 讲稿 + 问答准备 → 逐页回填 locator 与逐字 quote → 渲染导出与自检。

## 2. 输入契约（先发现，再假设）

运行时只读输入：
- `/input/case.json`：案例标识、任务约束、材料清单；若含 `output_contract`，**原样保留其全部字段**（缺值给 `null`/`[]`，不改名不删未知键）。
- `/input/task.md`：任务说明。
- 材料文件（PDF/图片/模板/起始 deck）按 `case.json` 清单定位，通常位于 `/input/` 或 `/app/work/`。
- 可用工具：`pdf-text-tool`、`pdf-render-tool`、`ocr-tool`、`latex-compiler`、`office-renderer`、`browser-automation`、`font-discovery`、`scientific-python`。

工作目录 `/app/work/`，最终产物写入 `/app/output/`。扫描输入目录不假设固定文件名；文本按 `utf-8-sig` 读取并容错（BOM/坏编码/坏 JSON 降级不崩溃），单文件超 2MB 跳过并记入 `sources`。

## 3. 输出契约

必须产出 `/app/output/result.json`，含：
- `case_id`（优先 `case.json.id`，其次 `armiId`）与题目 `output_contract` 的全部字段（平铺到顶层并保留 `output_contract` 快照）；
- `status`：`complete` / `partial` / `insufficient_evidence` / `input_error` / `failed`（诚实标注）；
- `sources`：实际读取的来源清单（路径、类型、是否读取、失败原因）；
- `uncertainty`：无法判断、材料缺失、证据不足的逐条说明；
- `pending_human`：需要人工决策/补料的缺口清单；
- `change_log`：时间戳 + 动作 + 影响范围的增量记录。

若 `case.json` 未给出 `output_contract`，使用以下默认契约（字段定义见 `references/output_contract.md`）：

- `report_goal`：可检查的报告目标（目标/听众/时长/页数）。
- `audience`：听众先验、关注点、可能质疑。
- `narrative`：页面序列与取舍理由（非按论文目录搬运）。
- `pages`：页级结构数组，每页含 `page_index`、`title`、`single_claim`、`evidence_strength`（high/medium/low）、`confidence`（established/indicated/speculative/not_reported）、`figures`、`speaker_notes`、`source_refs`。
- `claims`：证据台账，每条含 `claim`、`locator`（来源定位）、`quote`（逐字原文，未改写）、`status`（verified/pending/conflict）。
- `editable_deck`：可编辑演示文件（路径、格式、所用模板）。
- `evidence_map`：每页主张 → 来源定位与逐字 quote 的映射。
- `qa_prep`：机制/数据/对照/局限四类追问、口径一致回答、可引用页面。
- `rendering`：源文件、导出件（PDF/PNG 预览）、字体、模板、修改记录。
- `limitations`：缺图/低分辨率/公式丢失/字体缺失/模板冲突/导出失败/超时等的显式声明。

可编辑演示文件（PPTX/HTML/ODP）与渲染预览（PDF/PNG）一并写入 `/app/output/`。未产出的字段留空或显式 `null`，不得编造。

## 4. 证据溯源（硬要求）

每个关键结论/数值/图元都写入 `claims` 台账：`claim` + `locator` + 逐字 `quote`。quote 必须来自实际读取的源材料原文，不得转述或补造。同一指标来源不一致时，两个都记录并标 `source_conflict`。无法定位的事实不写为结论，标 `pending_human` 或 `not_reported`。

## 5. 执行流程（总预算 900 秒、64 次模型请求）

每步输出先落盘、后改进。**前 180 秒内必须用 `scaffold.py` 写出 `/app/output/result.json` 骨架**，此后只做增量改进，任何中途异常都留下合法 partial 结果。

| 步骤 | 输入 | 输出 | 时间预算 |
|-|-|-|-|
| 1 读取核对 | case.json、task.md、材料 | 材料清单、约束、output_contract | ≤60s，3–5 请求 |
| 2 落盘骨架 | 步骤1的 case_id/契约 | `scaffold.py` 生成 result.json | ≤120s，1–2 请求 |
| 3 主线规划 | 材料全文 | report_goal/audience/narrative/pages 初稿 | 120–300s，8–15 请求 |
| 4 生成演示件 | pages+模板 | 可编辑 deck + speaker_notes | 300–540s，10–20 请求 |
| 5 证据映射 | pages+来源定位 | evidence_map，跑 `evidence_check.py` | 540–660s，5–10 请求 |
| 6 渲染自检 | deck+导出件 | PDF/PNG 预览，跑 `selftest.py` | 660–780s，4–8 请求 |
| 7 增量收尾 | 自检报告 | 修复缺陷、更新 change_log | 780–900s，余量 |

- 每步用 `scaffold.py --update` 把新字段合并进 result.json。
- 请求数为上限，不追求用完；到 850 秒即收尾写最终 result.json。

## 6. 自检清单（对应评分维度，逐条可勾选）

- [ ] 实际效果：report_goal/audience/narrative/pages/qa_prep 齐全闭环；每页恰好一条 single_claim；数字与图表引用与源材料一致；evidence_strength/confidence 与证据匹配；六方面齐全。
- [ ] 证据可核验：claims/evidence_map 每条有 locator + 逐字 quote，`evidence_check.py` 无 error。
- [ ] 使用增益：取舍决策写入 narrative/change_log；导出修正次数与来源已记录。
- [ ] 创新增益：audience 影响页数与术语深度；pages 有唯一主张+证据+图表绑定；qa_prep 覆盖机制/数据/对照/局限。
- [ ] 可靠体验：可编辑文件 + PDF/PNG 预览存在，`selftest.py` 无阻断项。
- [ ] 异常处理：缺图/低分辨率/公式丢失/字体缺失/导出失败/超时均已写入 limitations。
- [ ] 生态贡献：脚本仅标准库、相对路径调用、`--help` 可查；references 说明完整。

## 7. 降级与不确定性策略（本版重点）

- 材料缺失/无法定位：保留 single_claim，evidence_strength 标 low、confidence 标 not_reported，写入 uncertainty 与 pending_human，不补造出处。
- 缺图/低分辨率：页面注明“图缺失/不可用”，写入 limitations，不生成伪造图。
- 公式/符号丢失：保留原文 quote 并标注“渲染缺失”，不臆造公式。
- 字体/模板/office-renderer 不可用：降级为 HTML deck 或纯文本大纲，status 标 partial，说明原因。
- 导出失败/超时：保留可编辑源文件与部分结果，status 如实标 partial/failed，记入 change_log。
- 冲突数据：各来源都记录并标 source_conflict，不下单方结论。
- 禁止：编造评测分数、编造运行结果、补造缺失软件状态或科学数据。

## 8. 脚本调用（相对路径，仅标准库）

```bash
python3 scripts/scaffold.py --input /input/case.json --task /input/task.md --output /app/output
python3 scripts/scaffold.py --update --result /app/output/result.json --merge /app/work/step3.json
python3 scripts/evidence_check.py --result /app/output/result.json --work /app/work
python3 scripts/selftest.py --skill <SKILL根目录>
```

脚本仅依赖 Python 标准库，路径相对 SKILL.md 所在目录，均支持 `--help`。字段定义见 `references/output_contract.md`，检查清单见 `references/checklist.md`。

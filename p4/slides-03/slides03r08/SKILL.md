---
name: evidence-first-talk-organizer
description: 把论文或研究材料组织成证据可追溯、可编辑、符合母版模板的学术汇报（评分维度逐条对齐到流程与自检），产出逐页唯一主张、图表引用、讲解顺序、讲稿备注、问答准备与渲染检查。用于把固定科研案例做成可上台、可复核的演示文件并保留页级证据映射（locator + 逐字 quote）。
version: 1.1.0
---

# 学术汇报内容组织 Skill（评分维度对齐版）

## 1. 角色与何时使用

本 Skill 负责“从已有研究材料到可讲页面”的组织与交付，不负责论文精读、学术写作成稿、科研绘图素材生产（这三类由相邻课题承担）。任一条件满足即触发：

- 任务要求把论文 / 研究材料组织成演示文稿，且需逐页标注主张、图表与证据来源；
- 输入含听众、时长、页数、母版模板等约束，需要内容取舍与叙事顺序设计；
- 评分要求证据可追溯、导出可打开、结论强度与证据匹配。

不适用：论文精读总结、学术写作成稿、科研绘图本身的素材生产。

核心方法（评分对齐闭环）：先定可检查的报告目标 → 提取“问题—方法—结果—结论”主线并按听众取舍（不按论文目录搬运）→ 每页唯一主张 + 支撑证据（locator + 逐字 quote）+ 证据强度 + 图形表达 + 讲稿备注 → 预判机制/数据/对照/局限四类追问 → 导出可编辑文件 + 真实预览并逐页回填证据定位 → 用 `scripts/selftest.py` 把评分维度逐条对到自检项，失败即修复再交付。

## 2. 输入契约（运行时只读）

- `/input/case.json`：案例标识、任务约束、材料清单；可能含 `output_contract`，若存在必须**原样保留全部字段**（含未知键），缺值给 `null`/`[]`。
- `/input/task.md`：任务说明。
- 材料文件（PDF / 图片 / 母版模板 / 起始 deck）按 `case.json` 清单定位，通常位于 `/input/` 或 `/app/work/`。
- 可用工具：`pdf-text-tool`、`pdf-render-tool`、`ocr-tool`、`latex-compiler`、`office-renderer`、`browser-automation`、`font-discovery`、`scientific-python`。

工作目录 `/app/work/`；最终产物写入 `/app/output/`。**读取实际文件内容，不以文件名标签臆断来源**；PDF 解析拿到的是原始 PDF，没有预提取的答案层。

## 3. 输出契约

必须产出 `/app/output/result.json`，包含：

- `case_id`：案例标识（优先 `case.json.id`，其次 `armiId`）；
- 题目 `output_contract` 的**全部字段**（原样保留、不改名、不删未知键）；
- 附加字段：`status`、`sources`、`uncertainty`、`pending_human`、`change_log`、`errors`、`degradations`、`runtime`。

若 `case.json` 未给出 `output_contract`，使用默认契约（字段定义见 `references/output_contract.md`）：`case_id`、`report_goal`、`audience`、`narrative`、`pages`、`editable_deck`、`evidence_map`、`qa_prep`、`rendering`。

每个关键结论（数值 / 图元 / 主张）必须有 `locator`（章节 / 图 / 表 / 页码）与 `quote`（源材料**逐字**片段），不得改写事实、不得补造出处。可编辑演示文件（PPTX / HTML / ODP）与渲染预览（PDF / PNG）一并写入 `/app/output/`；未产出的字段留空或显式 `null`。

## 4. 评分维度 → 流程 → 自检对齐表（本 Skill 核心设计）

| 评分维度(分值) | 落到哪一步 | 落到哪些字段 | 自检脚本/清单 |
|-|-|-|-|
| 实际效果·任务完成度(15) | 步骤 3 主线规划 | report_goal / audience / narrative / pages / qa_prep | selftest 闭环项 |
| 实际效果·结果正确性(15) | 步骤 4–5 | pages.figures / pages.quote / evidence_map | evidence_check |
| 实际效果·完整性(5) | 步骤 3–6 | 六方面全字段 | selftest coverage |
| 实际效果·可核验(5) | 步骤 5–6 | evidence_map / sources / change_log | evidence_check + render |
| 使用增益·质量(15) | 步骤 3 | narrative 取舍理由 | checklist |
| 使用增益·效率(5) | 步骤 2 / 7 | change_log 返工与导出修正记录 | checklist |
| 使用增益·可验证(5) | 步骤 7 | change_log 对照记录 | checklist |
| 创新增益·新增能力(15) | 步骤 3 | audience 适配 / 页级规划 / 问答预演 | checklist |
| 可靠体验(10) | 全部 + 降级 | status / uncertainty / pending_human / preview | selftest reliability |
| 生态贡献(10) | 交付 | SKILL / references / scripts | 文件齐备检查 |

完整逐条清单见 `references/checklist.md`。

## 5. 执行流程（总预算 900 秒、64 次模型请求）

**前 180 秒（3 分钟）内必须用 `scripts/scaffold.py` 写出 `/app/output/result.json` 骨架（status=partial）**，此后只做增量改进：每步用 `scripts/scaffold.py --update` 合并新字段，保证任何中断都留下合法产物。

| 步骤 | 输入 | 输出 | 时间 | 请求 |
|-|-|-|-|-|
| 1 读取核对 | case.json / task.md / 材料 | 约束清单 + output_contract | ≤60s | 3–5 |
| 2 落盘骨架 | 步骤 1 结果 | scaffold.py 生成 result.json 骨架 | ≤120s | 1–2 |
| 3 主线规划 | 材料全文 | report_goal / audience / narrative / pages 初稿 | 120–300s | 8–15 |
| 4 生成演示件 | pages + 模板 | 可编辑 deck + speaker_notes | 300–540s | 10–20 |
| 5 证据映射 | pages + 来源 | evidence_map(locator + quote)，跑 evidence_check | 540–660s | 5–10 |
| 6 渲染自检 | deck + 导出 | PDF/PNG 预览，跑 selftest | 660–780s | 4–8 |
| 7 增量收尾 | 自检报告 | 修复缺陷、更新 change_log | 780–900s | 余量 |

请求数为预算上限，不追求用完；到 850 秒即收尾并写最终 result.json。

## 6. 自检清单（逐条勾选，对应评分维度）

运行 `python3 scripts/selftest.py --result /app/output/result.json --work /app/work --output /app/output` 自动核验（输出逐项通过/未通过，**不产生评测分数**）。

- [ ] 实际效果：report_goal / audience / narrative / pages / qa_prep 齐全且形成闭环。
- [ ] 每页恰好一条 `single_claim`，无摘要 / Results 逐段搬页。
- [ ] 每页数字、图表引用与源材料一致；`evidence_strength` 与证据匹配。
- [ ] 六方面齐全：论文主线、页结构、图表引用、讲解逻辑、模板约束、导出渲染。
- [ ] evidence_map 每页含 `locator` + 逐字 `quote`，`evidence_check.py` 无 error。
- [ ] 使用增益：取舍决策与返工 / 导出修正次数写入 `change_log`，可复核。
- [ ] 创新增益：audience / pages / qa_prep 均有落地字段，非空话。
- [ ] 可靠体验：可编辑文件 + PDF/PNG 预览存在，selftest 无阻断项。
- [ ] 异常处理：缺图 / 低分辨率 / 公式丢失 / 字体缺失 / 导出失败 / 超时均如实标注。
- [ ] 生态贡献：脚本仅标准库、相对路径调用，references 说明完整。

## 7. 降级策略

- 材料缺失或无法定位来源：保留 `single_claim`，`evidence_strength` 标 `low`，记入 `uncertainty`，不得补造出处。
- 图表缺失或低分辨率：页面注明“图缺失 / 不可用”，记入 `rendering`，不生成伪造图。
- 字体 / 模板 / office-renderer 不可用：退回纯文本大纲或 HTML deck，`status` 标 `partial`，说明降级原因。
- 导出失败或超时：保留可编辑源文件与部分结果，`status` 如实标 `partial` / `failed`，记入 `change_log`。
- 缺失软件状态或科学数据：只标 `limitations` / `pending_human`，**禁止编造**。

## 8. 脚本调用（相对路径，仅标准库）

```bash
python3 scripts/scaffold.py --input /input/case.json --task /input/task.md --work /app/work --output /app/output
python3 scripts/evidence_check.py --result /app/output/result.json --work /app/work
python3 scripts/selftest.py --result /app/output/result.json --work /app/work --output /app/output
```

脚本路径相对 SKILL.md 所在目录；均可用 `--help` 查看参数。字段定义见 `references/output_contract.md`，评分对应清单见 `references/checklist.md`。

## 9. 最小调用示例（示意，非正式案例结果）

输入 `case.json` 最小结构（示意，字段为占位）：

```json
{"id": "case-1", "output_contract": {"case_id": "case-1"}}
```

调用（相对 SKILL.md 目录）：

```bash
python3 scripts/scaffold.py --input /input/case.json --task /input/task.md --work /app/work --output /app/output
python3 scripts/evidence_check.py --result /app/output/result.json --work /app/work
python3 scripts/selftest.py --result /app/output/result.json --work /app/work --output /app/output
```

示意输出 `result.json`（截取，字段值均为占位示例）：

```json
{"case_id": "case-1", "status": "partial", "report_goal": null, "pages": [],
 "evidence_map": [], "qa_prep": [], "sources": [], "uncertainty": [],
 "pending_human": [], "change_log": [{"action": "scaffold"}]}
```

以上仅为接口示意，不含任何正式案例的科学内容。


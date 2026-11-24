---
name: evidence-traced-talk-organizer
description: 把论文或研究材料组织成证据可逐字溯源、可编辑、符合模板的学术汇报（逐页唯一主张、图表引用、讲解顺序、讲稿备注、问答准备、渲染检查、片段级证据映射）。用于把固定科研案例做成可上台、可复核的演示文件，并让每个关键结论都带 locator 与逐字 quote。
version: 1.0.0
---

# 学术汇报内容组织 Skill（证据溯源强化版）

## 1. 角色与何时使用

本 Skill 让 Agent 在“从已有研究材料到可讲页面”环节稳定产出可复核结果，且每个论断可逐字指回源材料。任一条件满足即触发：

- 任务要求把论文 / 研究材料组织成演示文稿，且需逐页标注主张、图表与证据来源；
- 输入含听众、时长、页数、模板等约束，需要内容取舍与叙事顺序设计；
- 评分要求证据可追溯、导出可打开、结论强度与证据匹配。

不适用于：论文精读总结、学术写作成稿、科研绘图素材生产（相邻课题负责）。

核心方法：**证据先行**。先抽取“可直接粘贴的逐字片段”，再据片段写出每页唯一主张；主张不得改写事实。先定报告目标与听众，再定“问题—方法—结果—结论”主线与页级取舍；每页一条唯一主张 + 支撑证据 + 证据强度，配套讲稿与问答准备；最后逐页回填片段级来源定位并检查渲染导出。

## 2. 输入契约

运行时原始输入（只读，勿改）：

- `/input/case.json`：案例标识、任务约束、材料清单；可能含 `output_contract`，若存在则**原样保留全部字段**，未知键不得删除或改名。
- `/input/task.md`：任务说明与运行契约。
- 材料文件（PDF / 图片 / 模板 / 起始 deck）按 `case.json` 清单定位，通常位于 `/input/` 或 `/app/work/`。
- 可用工具：`pdf-text-tool`、`pdf-render-tool`、`ocr-tool`、`latex-compiler`、`office-renderer`、`browser-automation`、`font-discovery`、`scientific-python`。

文本按 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 都降级不崩溃）；单文件 >2MB 跳过并记入 `degradations`。工作目录 `/app/work/`；最终产物写入 `/app/output/`。

## 3. 输出契约

必须产出 `/app/output/result.json`，含 `case_id`（优先 `case.json.id`，其次 `armiId`）、`output_contract` 的全部字段，并附 `status`、主产物路径、`sources`、`uncertainty`、`pending_human`、`errors`、`degradations`、`change_log`。若 `case.json` 未给出 `output_contract`，使用默认契约（字段定义见 `references/output_contract.md`）：

- `case_id`、`status`（`complete | partial | failed | insufficient_evidence | input_error`）、主产物路径。
- `report_goal`：可检查的报告目标（目标、听众、时长、页数）。
- `audience`：听众先验、关注点、可能质疑。
- `narrative`：页面序列与取舍理由（非按论文目录搬运）。
- `pages`：页级结构数组，每页含 `page_index`、`title`、`single_claim`（唯一主张）、`evidence_strength`（high/medium/low）、`figures`、`speaker_notes`、`source_refs`。
- `editable_deck`：可编辑演示文件（路径、格式、所用模板）。
- `evidence_map`：**片段级证据映射**（本版强化点，结构见第 4 节）。
- `qa_prep`：预判追问、口径一致回答、可引用页面。
- `rendering`：源文件、导出件（PDF/PNG 预览）、字体、模板、修改记录。
- `sources`：实际读取的来源清单（路径、类型、是否读取、页数）。
- `uncertainty` / `pending_human` / `errors` / `degradations`：逐条说明缺口。
- `change_log`：时间戳 + 动作 + 影响范围的增量记录。

可编辑演示文件（PPTX / HTML / ODP 等）与渲染预览（PDF / PNG）一并写入 `/app/output/`。未产出的字段留空或显式标 `null`，不得编造。

## 4. 证据溯源规范（本版强化点）

`evidence_map` 是**片段级**数组，每条对应一个可独立复核的论断片段，而非每页一条粗记录：

```json
{
  "page_index": 0,
  "claim": "本方法在 XXX 上相较 baseline 提升 12%",
  "evidence": [{
    "fragment": "提升 12%",
    "source_ref": "paper.pdf",
    "locator": {"type": "page", "page": 3, "section": "Results", "figure_or_table": "Table 1"},
    "quote": "逐字粘贴的原文片段……",
    "quote_offset": 1280,
    "strength": "high",
    "status": "verified"
  }]
}
```

硬性要求：

1. 每个关键结论 / 数值 / 图元，都要 `locator`（文件 + 页码 + 章节 + 图表号，至少两项）与 `quote`（逐字原文，不缩写、不转述）。
2. `quote` 必须来自实际读取的文本；`quote_offset` 为该片段在抽取文本中的字符起点，便于独立复核。
3. `strength` 与证据匹配：结果对比用 `high`，机制推测用 `low`，单点观察用 `medium`。
4. 同一指标两个来源不一致时，两条都记录并标 `status: "source_conflict"`，不隐藏冲突。
5. 缺失即标 `status: "missing"` 并记入 `uncertainty` / `pending_human`；禁止补造出处、DOI、数值。

## 5. 内容组织规则（可落地）

页数与时长换算：1 页 ≈ 45–60 秒讲解，页数 ≈ 时长（秒）÷ 50，另留封面页与结尾页各 1 页；页数超出时先砍“背景铺垫”与“次要结果”，不砍结论与证据链。

听众适配三型（决定背景比例、术语深度、证据展示深度）：

- 组会：讲清方法与进度风险；背景 1 页、方法约 40%、结果约 40%、风险与下一步约 20%；术语按组内共识，不展开公式推导。
- 答辩：讲清创新点与边界；问题 1 页、创新点约 2 页、结果对照约 2 页、局限 1 页；术语从简并给一句话解释。
- 评审：讲清结论与证据强度；结论先行，每结论紧跟支撑证据与 strength；重证据链完整，不炫方法细节。

single_claim 写法：

- 一句可讲结论，动词开头，最多 1 个数字；先有逐字 quote，再据此写 claim，禁止先写结论再找证据。
- 含“可能 / 或许 / 提示 / 初步”等措辞时，`evidence_strength` 同步降为 low 或 medium。
- claim 与 quote 数字必须一致；数字单位、样本口径、对照组名称不得缩写改写。

图表绑定：每页最多 1 主图 + 1 副表；`figures` 必须与 `source_refs` 对应，不得复用与页内主张无关的图；图缺失时不补图，页面注明“图缺失”。

问答预演四类（`qa_prep` 必须覆盖）：机制（为什么有效）、数据（样本 / 指标口径）、对照（与哪些 baseline 比、是否公平）、局限（未做什么、可迁移性）。

## 6. 执行流程（总预算 900 秒、64 次模型请求）

每步先落盘，后改进。**前 180 秒内必须先写出 `/app/output/result.json` 骨架**（步骤 2），此后只做增量改进。

| 步骤 | 输入 | 输出 | 时间预算 |
|-|-|-|-|
| 1 读取与核对 | case.json、task.md、材料 | 材料清单、约束、output_contract | ≤60s，3–5 请求 |
| 2 落盘骨架 | 步骤 1 的 case_id / 契约 | `scripts/main.py scaffold` 生成 partial 骨架 | ≤120s，1–2 请求 |
| 3 主线与规划 | 材料全文 | report_goal / audience / narrative / pages 初稿 | 120–300s，8–15 请求 |
| 4 生成演示件 | pages + 模板 | 可编辑 deck + speaker_notes | 300–540s，10–20 请求 |
| 5 证据映射 | pages + 逐字片段 | evidence_map（片段级），跑 `main.py evidence-check` | 540–660s，5–10 请求 |
| 6 渲染与自检 | deck + 导出件 | PDF/PNG 预览，跑 `main.py validate` | 660–780s，4–8 请求 |
| 7 增量收尾 | 自检报告 | 修复缺陷、更新 change_log | 780–900s，余量 |

- 每步用 `scripts/main.py update --patch-file <json文件>` 把新字段合并进 result.json（大补丁写文件避免命令行拆分；小补丁可用 `--patch '<json>'`），保证骨架始终最新。
- 步骤 3 同时把每个材料的抽取文本写入 `/app/work/sources.json`（`{"sources":[{"path":…,"text":…}]}`），供 `evidence-check` 校验 quote 逐字性。
- 请求数为预算上限，不追求用完；到 850 秒即收尾并写最终 result.json。

## 7. 自检清单（对应评分维度）

- [ ] 实际效果：report_goal、audience、narrative、pages、qa_prep 齐全且形成闭环（40 分）。
- [ ] 每页恰好一条 `single_claim`，无摘要逐段搬页。
- [ ] 每页数字、图表引用与源材料一致；`evidence_strength` 与证据匹配。
- [ ] 六方面齐全：论文主线、页结构、图表引用、讲解逻辑、模板约束、导出渲染。
- [ ] 证据可核验：evidence_map 每条均含 locator + 逐字 quote，`evidence-check` 无 error。
- [ ] 使用增益：相同输入下减少人工返工，取舍决策写入 narrative / change_log 可复核。
- [ ] 创新增益：听众适配、页级规划、问答预演、片段级溯源均有落地字段（非空话）。
- [ ] 可靠体验：可编辑文件 + PDF/PNG 预览均存在，`validate` 无阻断项。
- [ ] 异常处理：缺图 / 低分辨率 / 公式丢失 / 字体缺失 / 导出失败 / 超时均如实标注。
- [ ] 生态贡献：脚本只用标准库、相对路径调用，references 说明完整。

## 8. 降级策略

- 材料缺失或无法定位来源：保留 `single_claim`，`evidence_strength` 标 `low`，证据 `status` 标 `missing`，记入 `uncertainty`，不得补造出处。
- 图表缺失或低分辨率：页面注明“图缺失 / 不可用”，记入 `rendering`，不生成伪造图。
- 字体 / 模板 / office-renderer 不可用：退回纯文本大纲或 HTML deck，`status` 标 `partial`，说明降级原因。
- 导出失败或超时：保留可编辑源文件与部分结果，`status` 如实标 `partial` / `failed`，记入 change_log。
- 缺失软件状态或科学数据一律只标注 limitations，禁止编造。

## 9. 脚本调用（相对路径，仅标准库）

```bash
python3 scripts/main.py scaffold --case /input/case.json --task /input/task.md --out /app/output
python3 scripts/main.py update --result /app/output/result.json --patch-file /app/work/patch.json
python3 scripts/main.py evidence-check --result /app/output/result.json --sources /app/work/sources.json
python3 scripts/main.py validate --result /app/output/result.json --out /app/output
python3 scripts/selftest.py
```

脚本只依赖 Python 标准库，路径相对 `SKILL.md` 所在目录；均可用 `--help` 查看参数。字段定义见 `references/output_contract.md`，评分对应清单见 `references/checklist.md`。

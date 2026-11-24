---
name: paper-structure-review
description: 科研论文"结构与逻辑优化"写作 Skill。围绕研究问题与贡献主张对齐、章节结构与叙事顺序、论证链与论据对应、段落关系、重复内容、结论边界六个维度，对完整稿件做可定位（章节/段落/句子）的结构诊断，并输出可执行编辑动作（移动/合并/拆分/收窄/补过渡/重排），每条判断回指原文 locator 与逐字 quote。事实与来源映射、版本边界与人工确认项写入 result.json，最终写入 /app/output/result.json。适用于论文结构诊断、改稿方案、论证链核查与结论边界检查；不适用于需要新增实验或补写结论所缺证据的改写任务。
version: 1.0.0
---

# 论文结构与逻辑优化

## 1. 角色与何时使用

本 Skill 在以下场景启用：任务要求对一篇完整科研稿件做结构诊断并交付作者可直接执行的修改方案；
材料通常为完整稿件（Markdown/LaTeX/Word/PDF）、任务说明、受众与格式约束、参考文献或图表。
适用动作：结构诊断、改稿方案、论证链核查、重复内容识别、结论边界检查、逐条来源映射。

不适用：需要新增实验、补造数据或补写结论所缺证据来填补核心空白的任务——此时应如实报告材料缺口。

本 Skill 的突破口为"结构与逻辑"，而非语言润色：把问题定位到具体章节、段落乃至句子，说明它是
六维中的哪一类结构问题（诊得准）；给出可执行编辑动作（改得动）；逐条论断与论据闭环核查（链得通）；
每条判断回指原文位置与依据，作者可判断采纳/部分采纳/驳回（说得清）。六维规则见
`references/review-rules.md`，自检清单见 `references/checklist.md`。

## 2. 输入契约（运行时）

- 原始输入：`/input/case.json`（案例元数据、任务说明、材料清单、可能的 `output_contract`）与
  `/input/task.md`。
- 材料文件：`/input/` 下按清单提供的稿件 PDF、图片、表格、文本或文献；PDF 可能没有预解析层，
  必须实际读取文件内容，不能凭文件名或领域标签猜测。
- 工作目录：`/app/work`（中间产物）；最终产物目录：`/app/output/`。
- 脚本位于本 Skill 根目录的 `scripts/`，统一用相对路径 `python scripts/main.py` 调用；若 Harness
  从 `/app/work` 启动，先 `cd /skills/weekly-skill`。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8，合法 JSON），结构如下：

- `case_id`：取自 case.json 的 `case_id` 或 `id`，原样保留。
- 题目 `output_contract` 的全部字段：从 case.json 的 `output_contract`（或任务说明规定字段）读取
  键名，逐字段填写；若未显式给出，按默认契约输出 `diagnosis`（六维诊断）、`edit_plan`（可执行
  编辑动作）、`source_map`/`sources`（事实与来源映射）、`version_boundary`（修改差异与人工确认项）。
- `status`：`complete` / `partial` / `failed` / `in_progress` 之一。
- `diagnosis`：`summary` + `dimensions`（六维，每维 `status` 与 `issues[]`，每条 issue 含
  `dimension`/`issue_type`/`locator`/`quote`/`evidence`/`edit`）。
- `edit_plan`：数组，逐条 {id, type, target_locator, quote, action, reason, priority, acceptability}。
- `source_map`/`sources`：每条 {claim_id, claim, locator, quote, source_ref, confidence}；
  `source_ref` 写"文件+段落/图表编号/统计输出名"。
- `uncertainty`：逐条记录材料不足、冲突、未确认项及原因。
- `pending_human`：需人工确认或决策的事项。
- `version_boundary`：`material_gaps`、`unconfirmed`、`change_summary`。
- `change_log`：按时间顺序 {time, action, status}，至少含骨架落盘与每次增量修改。

可编辑产物（Markdown/JSON）写到 `/app/output/`；若产出 docx/图类产物，另存 PDF/PNG 预览。
`result.json` 允许多次增量覆盖，每次写入必须保持 JSON 合法、字段完整。

## 4. 时间预算与早落盘（本版核心）

总预算 900 秒、64 次模型请求、单响应 16384 token。**硬性要求：开始后 3 分钟内必须落盘一版
合法的 partial 结果骨架，之后只做增量改进，任何失败都不允许不落盘。** 阶段预算如下，模型请求
合计约 48 次，预留约 16 次用于增量改进：

| 阶段 | 时间 | 请求 | 动作 |
|---|---|---|---|
| 0 落盘骨架 | 0–1 min | ~2 | 读 case.json/task.md，确认 case_id、体裁、受众、格式、output_contract；跑 `scripts/main.py` 落盘 partial 骨架 |
| 1 盘点材料 | 1–4 min | ~6 | `scripts/main.py` 生成 evidence.json（候选章节/段落/论断句/数字/重复片段） |
| 2 六维诊断 | 4–7 min | ~10 | 逐维核对候选锚点，定位 issue 到 locator+quote，增量写回 diagnosis |
| 3 编辑方案 | 7–11 min | ~18 | 生成 edit_plan：可执行动作 + priority + acceptability |
| 4 闭环与边界 | 11–13.5 min | ~8 | 论证链闭环核查 + 结论边界比对，补齐局限 |
| 5 自检定稿 | 13.5–15 min | ~4 | 跑 `scripts/selftest.py` 与 verify，填 uncertainty/pending_human/change_log，置 status |
| 6 增量改进 | 剩余 | ~16 | 优先补来源映射与局限，不推翻已落盘内容 |

第 1 分钟内、最迟第 3 分钟，`/app/output/result.json` 必须存在且为合法 JSON（status=in_progress，
各字段空值/空数组，change_log 记第一条）。之后每次修改都是增量覆盖，绝不从零重写。

## 5. 六维结构诊断方法（核心创新）

诊断必须覆盖六维；某维不是稿件主要问题时写"未发现主要问题"而非留空。每维的问题类型、
判定依据与编辑动作见 `references/review-rules.md`。脚本 `scripts/core_impl.py` 只提供候选锚点
（章节、论断句、数字、重复片段、边界词），模型逐条核对原文后落盘，**只核对、不凭空生成**。

- research_question：研究问题与贡献主张对齐（是否提出、是否贯穿、贡献是否越界）。
- section_structure：章节结构与叙事顺序（顺序、篇幅、信息释放是否服务论证）。
- argument_chain：论证链与论据对应（逐条论断→论据，找断裂/跳跃/循环/强度不足）。
- paragraph_relations：段落关系（过渡、段落功能单一、合并/拆分）。
- redundancy：重复内容（同义/跨章节重复，区分有意呼应）。
- conclusion_boundary：结论边界（是否越界、是否遗漏限制、是否相关性写成因果）。

## 6. 执行流程

每步输入输出如下；预算见第 4 节；缺材料立即降级（第 8 节），不空转。

- 步骤 0（落盘骨架）：输入 case.json/task.md；输出 `/app/output/result.json` partial 骨架。
  运行 `python scripts/main.py`。
- 步骤 1（盘点材料）：输入 /input 文件；输出 `/app/work/evidence.json` 与材料清单。脚本按
  text/PDF/image/table 分类，PDF/图片若无法直接读取则标注"需 OCR/渲染"，不猜测。
- 步骤 2（六维诊断）：输入 evidence.json + 材料；逐维核对候选锚点，为每个 issue 建立
  locator + 逐字 quote，增量写回 `diagnosis`。
- 步骤 3（编辑方案）：输入 diagnosis；输出 `edit_plan`，每条含 type/target_locator/quote/action/
  reason/priority/acceptability。
- 步骤 4（闭环与边界）：输入 edit_plan + 原文；核对论证链闭环与结论边界，补齐局限与语气映射。
- 步骤 5（自检定稿）：运行 `python scripts/selftest.py` 与 verify 校验；修复 FAIL/WARN；置 status，
  填 uncertainty/pending_human/change_log；输出 `/app/output/verify_report.json`。

## 7. 自检清单

逐条勾选，对应评分维度；完整清单见 `references/checklist.md`。

- [ ] 实际效果：完整稿件已诊断，修改方案可直接执行；判断与原文相符，未虚构缺陷、未漏真实断裂、
      未把风格偏好当结构错误；六维均评估；每条判断可独立复核。
- [ ] 使用增益：起草前已运行脚本盘点和抽取候选锚点；骨架先行、增量改进，change_log 可查返工。
- [ ] 创新增益：论点—论据映射、跨章节冗余检测、结论—数据范围边界比对由脚本提供候选、模型核对。
- [ ] 可靠体验：触发明确、脚本可用、路径有效；空输入/坏 JSON/缺章节/无参考文献/图表缺失/稿件
      截断已识别并降级，未编造。
- [ ] 生态贡献：说明、依赖（仅标准库）、配置、范围、限制齐全；规则、清单、脚本可复用。

## 8. 降级策略

- 缺文件/缺软件（无 OCR、无 LaTeX、无渲染器）：在 `uncertainty` 与 `version_boundary.material_gaps`
  中如实标注"未提供/不可用"，相应字段留空并说明，status 置 `partial`；绝不伪造读数、软件状态或结论。
- 段落/图表读数不清、单位或引用不明：不猜测，标为待确认（`pending_human`），正文用稳健表述。
- 缺章节、无参考文献、图表/公式缺失、稿件截断：识别为具体缺口，逐条写入 material_gaps。
- 引用找不到：只写已有文献支持的论断，缺失部分标"无文献支持"。
- 运行失败或超时：保留已落盘 partial result.json（含 status/sources/uncertainty/change_log），
  说明失败原因。

## 9. 脚本调用（仅 Python 标准库）

- `python scripts/main.py`：skeleton → evidence → verify，一步落盘 partial 骨架并抽取证据。
- `python scripts/selftest.py`：空输入/坏 JSON/正常稿件三类自检，退出码 0 表示全 PASS。
- `python scripts/core_impl.py`：被 main/selftest 引用的共享实现，不单独运行。

环境变量可覆盖默认路径：CASE_JSON、TASK_MD、INPUT_DIR、WORK_DIR、OUTPUT_DIR、RESULT_JSON。
禁止用脚本或其他方式伪造评测分数或运行结果。

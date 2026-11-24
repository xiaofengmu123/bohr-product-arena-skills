---
name: manuscript-structure-review
description: 科研论文"结构与逻辑优化"Skill。对给定稿件做六维结构诊断（研究问题、章节结构、论证链、段落关系、重复内容、结论边界），把每个问题定位到章节/段落/句子并给出可执行编辑动作（移动到哪/与哪段合并/从哪拆分/哪句收窄），建立论点—论据映射与逐字溯源，最终写入 /app/output/result.json。适用于论文结构诊断、改稿、论证闭环核对与结论边界审查；不用于需要新实验/新数据/新文献来补核心空白的任务。
version: 1.0.0
---

# 论文结构与逻辑优化

## 1. 角色与何时使用

本 Skill 对科研论文（或其章节）做**结构诊断与可执行改稿**，交付"问题定位 + 编辑动作 + 逐字溯源"的可复核诊断报告，不是整篇改写稿。

适用：结构诊断、章节重排、论证链核对、重复内容识别、结论边界审查、改稿与中英对照。触发条件：任务要求优化/诊断/改写论文结构、论证或结论，且材料含稿件正文（PDF/Markdown/LaTeX/docx/txt）。

不适用：需新实验、新数据或新增文献检索来补核心空白——此时只报告缺口，不补造内容。**本 Skill 只做结构与逻辑判断，不做语言润色，也不把风格偏好当结构缺陷。**

## 2. 输入契约（运行时）

- 原始输入：`/input/case.json`（case_id、任务说明、材料清单、可能的 output_contract）与 `/input/task.md`。
- 材料文件：`/input/` 下按清单提供的稿件 PDF、图片、表格、统计输出、文献；PDF 可能无预解析层，必须实际读取，不能凭文件名或领域标签猜测内容。
- 工作目录 `/app/work`（中间产物）；最终产物 `/app/output/`。
- 脚本在本 Skill 根目录 `scripts/`，用相对路径 `python scripts/main.py ...` 调用；若 Harness 从 `/app/work` 启动，先 `cd /skills/weekly-skill`。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8 合法 JSON）：

- `case_id`：取 case.json 的 `case_id` 或 `id`，原样保留；缺失时回退并记 uncertainty。
- 题目 output_contract 全部字段：从 case.json 的 `output_contract` 读键名逐字段填写；无显式契约时按默认契约 `diagnosis`（结构诊断与修改方案，可编辑 Markdown 文本）、`source_map`（论点—论据与来源映射）、`version_boundary`（修改差异、人工确认项、材料缺口）、`artifacts`（可编辑文件与渲染预览路径）。
- `status`：`complete` / `partial` / `insufficient_evidence` / `input_error` 之一。
- `sources`：数组 {finding_id, dim, problem_type, locator, quote, evidence, severity, action, disposition, confidence}；locator 写"文件+章节/段落/句子"，quote 为原文逐字片段（规则见 references/review-rules.md）。
- `uncertainty`：材料不足、冲突、未确认项及原因。
- `pending_human`：需作者裁定的项（采纳/部分采纳/驳回）。
- `change_log`：按时间 {time, action, status}，至少含骨架落盘与每次增量修改。
- 可编辑产物（Markdown）写 `/app/output/`；docx/图类产物另存 PDF/PNG 预览。

`result.json` 允许多次增量覆盖，每次写入保持 JSON 合法、字段完整（脚本用原子替换）。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

硬性要求：**开始后 3 分钟内必须落盘一版 result.json 骨架（status=in_progress，各字段空值/[]），之后只做增量改进；任何失败都不允许不落盘。**

- 步骤 0（0–1 分钟）：读 case.json/task.md，确认 case_id、体裁、受众、格式、output_contract。运行 `python scripts/main.py skeleton` 生成骨架。
- 步骤 1（1–5 分钟）：盘点材料。运行 `python scripts/main.py evidence` 生成 /app/work/evidence.json：按文本/PDF/图片分类，抽取章节标题、候选论断、数值、引用锚点，标记无法直接读取的文件（图片需 OCR/渲染）。
- 步骤 2（5–9 分钟）：六维诊断。逐条把问题定位到章节/段落/句子，标注六维与问题类型（断裂/跳跃/循环/论据不足/重复/衔接缺失/边界越界/因果误述），每个判断附 locator + 逐字 quote，写回 result.json.sources 与 diagnosis 骨架。
- 步骤 3（9–12 分钟）：生成编辑动作。每个问题给"移动到哪/与哪段合并/从哪拆分/哪句收窄/补什么论据"的具体指令与 disposition。
- 步骤 4（12–14 分钟）：论点—论据映射与闭环核对。抽取"研究问题→方法→结果→结论"论断，映射支撑论据，标 sufficient/insufficient/gap/loop/leap（写 source_map）。
- 步骤 5（14–15 分钟）：自检与定稿。运行 `python scripts/main.py verify` 校验字段完整、quote 可回溯、六维覆盖、局限如实；修复 FAIL/WARN；status 置终值，填 uncertainty/change_log。可选 `python scripts/selftest.py` 验证脚本健壮性。

剩余时间做增量改进：优先补来源定位与局限，不推翻已落盘内容。缺材料立即降级（见第 6 节）。

## 5. 自检清单（逐条勾选，对应评分维度）

实际效果（40 分）：
- [ ] 已产出完整结构诊断与可执行修改方案，体裁/受众/格式符合 task（任务完成度）。
- [ ] 每个判断与稿件实际内容相符：不虚构缺陷、不漏真实断裂、不把风格偏好当结构错误（正确性）。
- [ ] 六类（研究问题、章节结构、论证链、段落关系、重复内容、结论边界）均已检查，缺项不是稿件主要问题所在（完整性）。
- [ ] 每个判断附 locator + 逐字 quote，可独立复核（可核验性）。

使用增益（25 分）：
- [ ] 起草前已用 evidence.py 盘点材料与候选锚点，而非凭记忆（质量/效率）。
- [ ] 骨架先行、增量改进，返工记录于 change_log（增益可验证）。

创新增益（15 分）：
- [ ] 已产出论点—论据映射表，标出断裂/循环/跳跃（新增能力）。
- [ ] 每个问题给了可执行编辑动作（移动/合并/拆分/收窄），非整篇另起稿（方法改进）。

可靠体验（10 分）：
- [ ] 空输入、坏 JSON、缺参数、超大文件均优雅降级不崩溃（稳定性）。
- [ ] 缺章节/无参考文献/图公式缺失/截断稿已识别并标注 limitations，未编造（异常处理）。

生态贡献（10 分）：
- [ ] 说明、依赖（仅 Python 标准库）、配置、范围、限制齐全（文档完整）。
- [ ] 六维规则、问题分类、检查清单、脚本可复用（可复用性）。

完整清单见 references/checklist.md；六维方法细则见 references/review-rules.md。

## 6. 降级策略

- 缺文件/缺软件（无 OCR、无 LaTeX、无渲染器）：在 uncertainty 与 version_boundary.limitations 标注"未提供/不可用"，字段留空并说明，status 置 partial/insufficient_evidence；绝不伪造读数、软件状态或科学数据。
- 图表读数不清、单位不明、误差棒含义无法确定：不猜测，标为待确认（uncertainty），正文用"[待确认：xxx]"。
- 引用/参考文献缺失：只写已有文本支持的论断，缺失标"无文献支持"。
- 运行失败/超时：保留已落盘 partial result.json（含 status、sources、uncertainty、change_log），说明失败原因。

## 7. 脚本调用（仅 Python 标准库）

- `python scripts/main.py skeleton`：生成骨架 result.json。
- `python scripts/main.py evidence`：盘点材料并抽取候选锚点 → /app/work/evidence.json。
- `python scripts/main.py verify`：校验契约与可回溯性 → /app/output/verify_report.json。
- `python scripts/selftest.py`：脚本健壮性自检（空输入/坏 JSON/超大文件/编码/确定性）。

环境变量覆盖默认路径：CASE_JSON、TASK_MD、INPUT_DIR、WORK_DIR、OUTPUT_DIR、RESULT_JSON。禁止用脚本或其他方式伪造评测分数或运行结果。

最小调用（示意输出，仅为说明接口，非正式案例结果）：

```text
$ python scripts/main.py skeleton
{"ok": true, "action": "skeleton", "result": "/app/output/result.json",
 "case_id": "case_x", "status": "in_progress", ...}        # 示意
$ python scripts/main.py evidence        # 产出 /app/work/evidence.json
$ python scripts/main.py verify          # 产出 /app/output/verify_report.json
```

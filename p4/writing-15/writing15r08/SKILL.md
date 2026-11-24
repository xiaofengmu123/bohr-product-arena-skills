---
name: long-doc-writing
description: >-
  学位论文与长文档写作工作流：读取给定科研材料，完成章节规划、生成/改写/校对、
  跨章节一致性与引用处理，并为每个论断保留来源与修改记录。当需要"依据给定材料
  生成或改写章节并交付可复核结果"时使用。
version: 1.0.0
---

# 学位论文与长文档写作 Skill

## 1. 角色与何时使用

本 Skill 把「材料 → 可编辑长文档 → 可复核交付」沉淀为可复用的写作流程。
当任务要求依据给定科研材料完成章节规划、生成、改写、校对、术语统一、引用补齐、
格式或结构化写作，并且每个论断/引用/改动都必须能回溯到原文、文献或版本记录时使用。

不适用：无输入材料的命题作文、一次性问答、不要求来源的随笔式写作。

四个硬目标（对应题目评分）：
- **立得住**：章节与提纲、论证链、字数分配一一对应，不是把材料堆成通顺段落。
- **连得上**：术语、定义、符号、编号、图表引用、前后结论跨章节一致；改一处能定位受影响段落。
- **改得动**：局部修改只动需要动处，保留作者原意，差异可比较、可回退、可说明。
- **查得清**：每个论断、引用、改动都能指回原文/文献/版本记录，他人可独立复核。

## 2. 输入契约

运行时原始输入固定位于：

| 路径 | 内容 |
|---|---|
| `/input/case.json` | 案例定义：`case_id`、任务要求、材料清单，可能含 `output_contract` 与编辑目标 |
| `/input/task.md` | 题目说明与文体/受众/格式约束 |
| `/input/organizer-edit-workorder.json` | 若存在：显式编辑目标与离线本地工作流范围（以实际文件为准） |
| `/input/materials/` 及 `/input` 下其他文件 | 事实底稿、分章可编辑原稿、PDF/笔记等科研材料 |

约束：**先发现再假设**——不要假设固定文件名/键名；文本按 `utf-8-sig` 读取并容错；单文件超 2MB 跳过并记录；PDF 用平台提供的 PDF 文本/渲染工具读取实际原文，不依据标签脑补内容。

## 3. 输出契约

最终产物写到 `/app/output/`，其中 `result.json` 为必交的结构化结果，字段如下：

- `case_id`：输入标识（必须回填）。
- 题目 `output_contract` 的全部字段：若 `case.json` 含 `output_contract`，逐键原样回填，缺值给 `null`/`[]`，不改名、不删未知键。
- `artifacts`：目标产物清单（可编辑 `.md`/`.docx` 等 + 渲染预览 `.pdf`/`.png`），每项含 `path/type/purpose`。
- `evidence_map`：事实与来源映射，每项含 `id/claim/kind/source/locator/quote/status`；`quote` 必须是材料逐字片段。
- `sources`：使用的源文件清单（`relpath/kind/role/char_count`）。
- `status`：`ok | partial | insufficient_evidence | input_error`，必须诚实。
- `uncertainty` / `pending_human` / `limitations` / `errors` / `degradations`：不确定项、待人工确认、缺口、异常与降级记录。
- `change_log`：每步动作（含时间、对象、原因），版本与修改记录在此可回溯。
- `runtime`：起止时间与阶段。

**先落盘再精修**：任何分析开始前，先调用 `scan` 写一份合法的 partial 骨架；中途异常也绝不留下不完整产物。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

| 步 | 动作（调用脚本用相对路径） | 输入 → 输出 | 时间预算 |
|---|---|---|---|
| 0 | `python3 scripts/main.py scan --input /input --output /app/output --work /app/work` | 输入目录 → `work/inventory.json` + partial `output/result.json` | **≤ 3 分钟** |
| 1 | 读 `inventory.json` 与实际材料，确定案例体裁/受众/格式/编辑目标 | inventory + 材料 → 写作计划 | ≤ 2 分钟 |
| 2 | 按提纲逐章写作/改写，产物写 `/app/output/`，证据写 `work/evidence.json`，状态与缺口写 `work/delivery_meta.json` | 材料 + 计划 → 可编辑章节 + 证据 | ≤ 5 分钟 |
| 3 | `python3 scripts/check.py --work /app/work --output /app/output` | 产物 → `work/check_report.json` | ≤ 1 分钟 |
| 4 | 依报告修复编号/交叉引用/术语/引用/重复问题，重跑 step 3 至无 error 级问题 | 报告 → 修订后产物 | ≤ 2 分钟 |
| 5 | `python3 scripts/main.py assemble --input /input --output /app/output --work /app/work` | 产物+证据+报告+变更 → 最终 `output/result.json` | ≤ 1 分钟 |

**关键纪律**：第 0 步必须在开始后 3 分钟内完成并落盘骨架；之后只做增量改进，每次模型请求前先确认当前 `result.json` 仍是合法 JSON。产物文本写在 `/app/output`，中间态（inventory/evidence/check 报告/变更日志）写在 `/app/work`。

## 5. 自检清单（逐条勾选，对应评分维度）

- [ ] 触发与易用性：Agent 按 `scan → 写作 → check → assemble` 完成一轮闭环，无额外手工步骤。
- [ ] 任务完成度：产出的是可打开、可编辑的具体章节，不是通用写作建议。
- [ ] 结果正确性：每条论断、数据、术语、编号、图表引用与原文/给定材料一致，不新增材料外事实。
- [ ] 结果完整性：提纲要求的章节与必要论证要素（研究问题/方法/结果/讨论/局限）及著录项齐全。
- [ ] 可核验性：`evidence_map` 每项带 `locator + quote`，`change_log` 可回溯。
- [ ] 跨章节一致性：`check_report.json` 已跑且 error 级问题已清零或如实标注。
- [ ] 运行稳定性：重复运行结果一致（`inventory.fingerprint` 与确定性检查保证）。
- [ ] 异常处理：空输入、坏 JSON、缺软件均记录到 `errors/degradations/limitations` 并降级，不崩溃。
- [ ] 资源可复用：术语表、引用表、检查清单等以模板形式随包提供，可被他人直接复用。

## 6. 降级策略

- 无材料 / 材料为空 → `status=insufficient_evidence`，`limitations` 列出缺口，**不编造章节或数据**。
- 引用找不到原文、章节结论冲突、编号不连续、文档格式不支持、超长截断 → 在 `uncertainty`/`pending_human` 如实标注，并写入 `check_report`。
- 缺 PDF/Office 渲染等软件 → 主文本仍以 `.md` 交付并保留预览说明，`degradations` 记录缺失能力，**不伪造渲染结果**。
- 缺参数 → 默认 `/input`、`/app/output`、`/app/work`；写盘失败 → 降级到最小合法产物，进程退出码保持 0。

## 7. 边界

一个 Skill 只解决「学位论文与长文档写作」这一类任务；没有真实材料就输出「材料不足」而非示例填充；交付前在干净环境自检一遍（见 `references/` 与 `scripts/main.py --help`）。

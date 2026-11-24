---
name: sci-mindmap
description: 把科研材料（论文/实验方案/项目计划/会议纪要）抽取为可编辑、可复核的思维导图：节点与关系有据、证据逐字可回溯、产出 Markdown/Mermaid/SVG/JSON 四格式。当任务要求"从材料整理成思维导图/结构化写作产物并保留来源"时使用。
version: 1.0.0
---

# 科研思维导图 Skill

## 1. 角色与何时使用

本 Skill 让 Agent 把给定科研材料稳定整理成**可打开、可读、可继续编辑、可复核**的思维导图。核心不是堆节点，而是：抓得准（核心概念/问题/方法/证据/结论/任务）、理得清（根节点唯一、层级与粒度一致）、连得对（关系有据、方向正确）、证据可追溯（逐字 locator + quote）、结果可编辑。

**何时使用**：输入是科研材料 + 写作/整理任务，要求产出思维导图或结构化写作产物。**不适用**：纯聊天问答、一次性脚本（直接写脚本即可）。

## 2. 输入契约

- 运行时原始输入在 `/input/case.json` 与 `/input/task.md`，材料可能在 `/input/materials/` 或同目录其他文件；**不假设固定文件名**，先扫描再读取。
- 文本按 `utf-8-sig` → `utf-8` → `gb18030` → `latin-1` 顺序容错读取；单文件 >2MB 跳过并记录。
- 若 `case.json` / `task.md` 给出 `output_contract`，必须**原样回填其全部键**：缺值给 `null`/`[]`，不改名、不删未知键。
- 产物写到 `/app/output/`，中间产物可放 `/app/work/`。

## 3. 输出契约

必须产出 `/app/output/result.json`，字段为：

- `case_id`：来自 `case.json` 或 `task.md` 的 challenge/case 标识（找不到则写 `"unknown"`）。
- 题目 `output_contract` 的全部字段（无显式契约时默认三组，见下）。
- 附加：`status`、`sources`、`uncertainty`、`change_log`。

默认契约三组字段（对应题面"目标产物 / 事实与来源映射 / 版本与边界"）：

1. `deliverable`：`{description, files}`，`files` 列出主产物相对路径。
2. `fact_source_map`：数组，每项含 `node_id`、`label`、`locator`、`quote`、`evidence_type`（`fact|summary|inference`）。
3. `version_boundaries`：`{changes, pending_human, material_gaps, conflicts, errors, degradations}`。

主产物（可编辑 + 可渲染）：`mindmap.md`（大纲）、`mindmap.mmd`（Mermaid 源码）、`mindmap.svg`（确定性矢量图）、`mindmap.json`（规范化结构）。SVG 本身可编辑可渲染；如需 PNG/PDF 预览，用平台 office-renderer / browser-automation 转换，失败则在 `limitations` 说明。

`status` 取值：`ok | partial | insufficient_evidence | input_error`；诚实标注，材料不足就 `insufficient_evidence`，不编造数据/结论/引用/DOI。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求 / 单响应 16384 token）

**Step 0 — 落盘骨架（0–3 分钟，≤8 请求）**
输入：`/input`。动作：运行 `python3 scripts/scaffold.py --input /input --output /app/output --work /app/work`，立即得到合法 `result.json`（partial）。读 `work/inputs_manifest.json` 确认发现到的文件清单。**此步必须先完成**，任何后续崩溃都保留可加载的 partial 产物。

**Step 1 — 抽取节点与关系（3–10 分钟，≤20 请求）**
输入：材料全文。逐段提取核心概念/问题/方法/证据/结论/任务/风险，按 `references/node-relation-schema.md` 确定节点类型与关系，为关键节点/关系配 `locator + quote`（逐字）。写入 `/app/work/mindmap.json`（schema 见 `scripts/render.py` 顶部注释）。

**Step 2 — 渲染与增量改进（10–14 分钟，≤28 请求）**
运行 `python3 scripts/render.py --spec /app/work/mindmap.json --output /app/output` 生成四格式产物。核对 SVG 布局、层级、关系方向；回填 `fact_source_map`、`version_boundaries`；每次改动后追加 `change_log`。

**Step 3 — 自检与收口（14–15 分钟，≤8 请求）**
运行 `python3 scripts/validate.py --output /app/output`，逐条修复 FAIL/WARN。据实设定最终 `status`；把剩余缺口写入 `pending_human` / `material_gaps` / `uncertainty`，不硬凑 `ok`。全程只做增量改进，不推翻已落盘的有效部分。

## 5. 自检清单（对应评分维度，逐条勾选）

- [ ] 读懂导图用途、目标读者、材料范围与交付格式（实际效果·完成度）
- [ ] 节点忠于材料、关系方向正确、证据挂在对应节点，无材料外连接（正确性）
- [ ] 覆盖问题/方法/证据/结论/任务/风险及必要层级分支（完整性）
- [ ] 关键节点与关系可回溯到 locator + quote，区分 fact/summary/inference（可核验性）
- [ ] 四格式产物均存在且可编辑：md 可读、mmd 可渲染、svg 可解析为 XML、json 可再导入（增益·效率）
- [ ] 相同材料与预算下，相对基础 Agent 减少返工：一次性落盘 + 规范化 schema + 确定性布局（增益·可验证）
- [ ] 抽取/主题归并/层级设计/关系判定/布局有明确规则而非自由发挥（创新·方法改进）
- [ ] `result.json` 含 case_id + 全部契约字段 + status/sources/uncertainty/change_log（可靠体验·稳定性）
- [ ] 缺数据/冲突/坏编码/节点爆炸均有识别与处置（可靠体验·异常处理）
- [ ] 未编造分数、结论、DOI、数值；未写预制答案（可靠体验/诚实性）

## 6. 降级策略

- **缺数据**：`status=insufficient_evidence`，逐条列出 `material_gaps`；数值未知写 `not_reported`，不填虚构值。
- **缺软件**（无 PDF/PNG 渲染工具）：SVG 即编辑+渲染产物；PNG/PDF 转换失败在 `limitations` 说明，不伪造渲染结果。
- **信息冲突**：两个来源都记录并标 `source_conflict`，各自带 locator + quote。
- **坏编码/坏 JSON/超大文件**：捕获后写入 `errors`/`degradations`，继续处理其余材料，进程不崩溃。
- **写盘失败**：降级到最小合法 `result.json`，`errors` 记录。
- **重复运行一致**：布局与产物内容确定（同一 spec 同输出）；时间戳为运行元数据，不参与内容判定。

脚本调用一律用相对路径（如 `scripts/scaffold.py`），仅依赖 Python 标准库；运行前用 `python3 scripts/scaffold.py --help` 验证可执行。

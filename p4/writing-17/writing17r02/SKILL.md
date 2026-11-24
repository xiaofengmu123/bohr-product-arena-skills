---
name: mindmap-skill
description: 为科研案例生成可复核、可编辑的思维导图：抽取核心概念/问题/方法/证据/结论/任务/风险，构建层级与关系，逐节点挂接来源，产出可编辑 SVG、FreeMind(.mm)、文本大纲与结构化 result.json。当需要把论文、综述、研究方案、项目计划、会议记录或知识体系整理成思维导图并保留证据溯源时使用。
version: 1.0.0
---

# 科研思维导图 Skill

## 1. 角色与适用场景
本 Skill 把给定科研材料整理为可打开、可编辑、可继续修改的思维导图，并保证每个关键节点与关系都能回溯到原文。适用五类子场景：论文/综述结构、实验与研究方案、项目与任务计划、会议纪要与决策、知识体系与概念关系。不适用：一次性聊天问答、无来源要求的装饰性导图、需要外部数据库但未提供的情况。

## 2. 输入契约
- 运行时输入目录固定为 `/input/`：`/input/case.json`（任务说明与 output_contract）、`/input/task.md`（本题说明）。材料位于 `/input/` 或其子目录（`materials/`、`papers/`、`*.pdf`、`*.txt`、`*.md`、`*.json` 等）。
- 先扫描发现材料，不假设固定文件名或键名；文本以 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 降级不崩溃）。PDF 用平台 pdf-text-tool 或 `pdftotext` 提取真实文本，必要时 ocr-tool；不依赖预提取答案层，不依据错误领域标签补造数据。
- 单文件超过 2MB 跳过并在 limitations 记录。工作目录 `/app/work/`，最终产物目录 `/app/output/`。

## 3. 输出契约（必须落盘）
`/app/output/result.json` 必须包含：
- `case_id`：取 `case.json` 的 case_id / id，缺失时用文件名。
- `status`：`ok | partial | insufficient_evidence | input_error`，如实取值。
- 回填题目 `output_contract` 的**全部键**（缺值给 `null`/`[]`，不改名、不删未知键）。
- 附加信封：`sources`、`uncertainty`、`change_log`、`errors`、`degradations`、`pending_human`、`runtime`。
- 主产物：`mindmap.svg`（可编辑矢量图）、`mindmap.mm`（FreeMind 可编辑）、`outline.md`（文本大纲）、`evidence_map.json`（证据映射，可直接用 mindmap.json 或单独生成）。若平台有渲染工具，从 SVG 渲染 `mindmap.pdf`/`mindmap.png` 供审阅；无法渲染则在 limitations 如实说明，不伪造二进制文件。

## 4. 中间表示（mindmap.json）
Agent 先生成 `/app/work/mindmap.json` 再交给脚本渲染与校验。节点字段：`id`、`label`、`kind`（root/concept/problem/method/evidence/conclusion/task/risk/gap/limitation）、`parent`、`note`、`evidence`、`inferred`。边字段：`id`、`from`、`to`、`relation`（causal/dependency/contrast/temporal/citation/support/conflict/part_of）、`label`、`evidence`。证据项：`locator`（如 `materials/paper.pdf#p3`）、`quote`（逐字）、`type`（fact/generalization/inference）。详细规范见 `references/node-relation-rules.md`。

## 5. 执行流程与时间预算
总预算 900 秒、64 次模型请求，单次回复含推理 ≤16384 token。分五步，每步先落盘、后优化：
1. （0–3 分钟）读 case.json 与 task.md，扫描材料，运行 `python3 -B scripts/result_builder.py --init --case /input/case.json --output-dir /app/output` 先写一版 result.json 骨架（status=partial）。此后任何中断都不留空产物。
2. （3–20 分钟）逐份精读材料，抽取核心概念、研究问题、方法、证据、结论、任务、风险，写 mindmap.json（先搭框架、后补证据）。
3. （15–60 分钟）层级与关系判定、证据挂接；运行 `python3 -B scripts/evidence_check.py --input /app/work/mindmap.json --materials-dir /input --report /app/output/evidence_report.json`，修正重复、环、无据连接、缺失 locator。
4. （30–70 分钟）运行 `python3 -B scripts/mindmap_render.py --input /app/work/mindmap.json --output-dir /app/output` 生成 SVG/MM/outline；用平台工具渲染 PDF/PNG。
5. （60–90 分钟，预留至 900s）运行 `python3 -B scripts/result_builder.py --finalize --case /input/case.json --mindmap /app/work/mindmap.json --report /app/output/evidence_report.json --output-dir /app/output --fill "mindmap=mindmap.svg"` 回填 contract 字段与来源，再 `--validate` 自检；剩余预算做增量改进并复检。

## 6. 自检清单（对应评分维度）
- [ ] 已确认导图用途、目标读者、材料范围与交付格式要求（任务完成度 15）。
- [ ] 每个节点忠于原文，关系方向正确，无材料中不存在的连接（结果正确性 15）。
- [ ] 关键概念/问题/方法/证据/结论/任务/风险已覆盖，层级与同级粒度合理（完整性 5）。
- [ ] 每个关键节点与关系带 locator+quote，可逐条回溯（可核验性 5）。
- [ ] SVG 可被 XML 解析、.mm 可打开、outline.md 可读；PDF/PNG 已渲染或如实标注缺失（可打开可编辑）。
- [ ] result.json 含 case_id + 全部 contract 字段 + status/sources/uncertainty/change_log。
- [ ] 同一输入重复运行结果一致（渲染确定性、固定顺序），未引入随机性（效率/稳定性）。
- [ ] 空输入、坏 JSON、坏编码、超长文件、缺渲染工具均已按降级策略处置（异常处理 3）。
- [ ] 未确认项写入 pending_human，缺口写入 limitations，未编造数值/引用/DOI（诚实性）。

## 7. 降级与诚实策略
- 材料缺失或无可读文本 → `insufficient_evidence` 并列出缺口；不示例填充、不编造。
- 节点/关系无原文依据 → 标 `inferred` 并写入 uncertainty，或删除该连接；关键结论无据时写入 pending_human。
- 同一指标多来源不一致 → 两侧都记录并标 `source_conflict`，不隐藏冲突。
- 缺软件/渲染失败/权限不足 → 写 limitations 与 degradations，保留 SVG/MM/outline 作为可编辑主产物。
- 写盘失败 → 降级为最小合法 JSON（仅 case_id+status+errors），进程不崩溃、退出码如实反映。

## 8. 脚本调用（相对路径，仅标准库）
- 初始化骨架：`python3 -B scripts/result_builder.py --init --case /input/case.json --output-dir /app/output`
- 结构校验：`python3 -B scripts/evidence_check.py --input /app/work/mindmap.json --materials-dir /input --report /app/output/evidence_report.json`
- 渲染产物：`python3 -B scripts/mindmap_render.py --input /app/work/mindmap.json --output-dir /app/output`
- 汇总收口：`python3 -B scripts/result_builder.py --finalize --case /input/case.json --mindmap /app/work/mindmap.json --report /app/output/evidence_report.json --output-dir /app/output --fill "mindmap=mindmap.svg"`
- 自检：`python3 -B scripts/result_builder.py --validate --result /app/output/result.json`
所有脚本用 `python3 -B` 运行，避免生成字节码缓存；路径均相对于 Skill 包根目录。

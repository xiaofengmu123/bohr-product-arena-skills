---
name: mindmap-academic-writing
description: 把科研材料（论文、综述、实验方案、项目计划、会议纪要、知识体系）抽取为可编辑、可预览、带证据溯源的思维导图，交付 FreeMind(.mm)+SVG 预览+大纲+证据映射与 result.json。当任务要求“围绕给定材料产出思维导图 / 结构化知识图 / 大纲并保留来源”时使用。
version: 1.0.0
---

# 科研思维导图 Skill（可复核、可编辑、留来源）

## 1. 角色与何时使用

本 Skill 把一类科研材料整理成**可打开、可继续编辑、逐条可溯源**的思维导图。
适用：任务明确要求围绕科研材料产出思维导图、知识结构图、研究路线、项目推进图、会议决策图或结构化大纲。
不适用：纯聊天问答、一次性制图、无材料来源的头脑风暴（材料不足时应如实返回 `insufficient_evidence`）。

核心原则：**先落盘合法骨架，再增量改进**；关键节点与关系必须挂 `locator` + 逐字 `quote`，区分事实(fact)、概括(summary)、推断(inference)；不新增材料外事实，不编造数据、引用或软件状态。

## 2. 输入契约

运行时固定输入：
- `/input/case.json` —— 案例信息，含 `case_id` 与题目 `output_contract`（字段名可能为 `output_contract`/`output_schema`/`result_schema`/`contract` 等，脚本自动发现并**原样回填全部键**，缺值给 `null`）。
- `/input/task.md` —— 任务说明与评分要求。
- `/input/` 下的材料（`materials/`、`*.pdf`、`*.md`、`*.txt`、`*.json` 等）。**读实际文件**，不依据文件标签假设内容；PDF 用平台提供的 pdf-text-tool / pdftotext 提取原文，再人工定位页码段落。

工作目录 `/app/work`，最终产物写 `/app/output/`。脚本默认参数已指向这三处，从 Skill 根目录（安装后为 `/skills/weekly-skill`）调用。

## 3. 输出契约

必须产出 `/app/output/result.json`，包含：
1. `case_id`；
2. 题目 `output_contract` 的**全部字段**（原样回填，不改名、不删未知键，未知值给 `null`/`[]`）；
3. 标准字段 `status`（`ok | partial | insufficient_evidence | input_error`）、`sources`、`uncertainty`、`change_log`，以及 `limitations`、`pending_human`、`errors`、`degradations`、`artifacts`、`runtime`。

主产物（由 `scripts/build_map.py` 生成）：
- `mindmap.mm` —— FreeMind 1.0.1 XML，可继续编辑（节点带 kind/证据 note，关系为 arrowlink）。
- `mindmap.svg` —— 分层树形预览，浏览器可看、矢量可改。
- `outline.md` —— 带 `⟦locator⟧` 与 quote 的大纲，人类可读可改。
- `evidence.json` —— 节点/关系 → locator+quote+evidence_class 的溯源映射。

## 4. 执行流程（总预算 900s、64 次模型请求）

**第 1 步｜落盘骨架（前 3 分钟内必须完成，约 60–90s）**
运行 `python3 -B scripts/scaffold.py --input /input --output /app/output --work /app/work`。
输入：`/input`。输出：`/app/output/result.json`（partial 骨架）、`/app/work/discovery.json`、`/app/work/map_spec.skeleton.json`。
读脚本输出，记录 `case_id`、`contract_keys`、输入文件清单。此步保证中途失败也有合法产物。

**第 2 步｜读材料（约 60–180s）**
读 `/input/task.md`、`/input/case.json` 原文；遍历 discovery 列出的材料；PDF 提取文本并标注页码段落 locator（如 `materials/paper.pdf#p3`）。冲突信息双侧保留。

**第 3 步｜抽取（约 180–300s，最耗模型请求）**
按 `references/schema_and_rules.md` 抽取：根节点+层级（理得清）、节点 kind（抓得准）、关系 type 与方向（连得对）、每条证据 locator+quote+class（证据可追溯）。写入 `/app/work/map_spec.json`（以 skeleton 为模板）。

**第 4 步｜渲染（约 30s，确定性）**
运行 `python3 -B scripts/build_map.py --spec /app/work/map_spec.json --output /app/output --work /app/work`。
输入：map_spec.json。输出：四个主产物 + `/app/work/artifacts.json`。

**第 5 步｜回填结果（约 30s）**
把 `output_contract` 各字段赋实际值；设置 `status`（诚实）；填写 `sources`（材料清单）、`uncertainty`（冲突/推断/缺失）、`change_log`（每步动作）、`limitations`、`pending_human`。

**第 6 步｜自检与增量改进（剩余预算 120–240s）**
运行 `python3 -B scripts/validate.py --output /app/output --work /app/work`，读 PASS/FAIL/INFO 报告；修复 FAIL 后重复第 3–5 步（有限轮，≤4 轮）；确认 `result.json` 合法、契约字段齐全、产物可解析后结束。

## 5. 自检清单（逐条可勾选）

- [ ] `result.json` 可解析，含 `case_id`，`status` ∈ {ok, partial, insufficient_evidence, input_error}
- [ ] `output_contract` 全部键原样在场，未知键未删除、未改名
- [ ] `mindmap.mm` 为合法 FreeMind XML；`mindmap.svg` 为合法 SVG；`outline.md`、`evidence.json` 存在
- [ ] 关键节点（concept/question/method/evidence/conclusion/task/risk/gap/decision/goal）均挂 locator+quote
- [ ] 关系方向正确、类型合法、端点存在；无材料依据的连接已删除或标 `pending_human`
- [ ] 同级粒度一致、无重复节点、无分类交叉；装饰词未被当作关键节点
- [ ] 数字、术语、引用、关键论断可逐条回溯；推断标 `inference`，未伪装成 `fact`
- [ ] 冲突信息双侧记录并标 `source_conflict`；缺数据/缺软件如实写入 `limitations`
- [ ] 同一输入重复运行产物一致（布局与顺序由 map_spec 决定，脚本确定性）

## 6. 降级策略

- 空输入/缺 `case.json`：`scaffold.py` 仍退出 0，写 `status=insufficient_evidence`，列出缺口，不编造章节。
- 坏 JSON/坏编码：`scaffold.py` 降级读取（utf-8-sig→utf-8→latin-1），记入 `errors`/`degradations`，继续处理其余材料。
- `map_spec` 缺失/为空：`build_map.py` 渲染单节点占位 + 明确 `limitations`，不崩溃。
- 节点规模爆炸：`build_map.py` 截断到 400 节点并在 `degradations` 记录。
- 缺软件/缺渲染能力：如实写 `limitations`，降级用已有标准库能力；**绝不声称存在未安装的软件状态**。
- 写盘失败：保留已写产物，写最小合法 result.json，进程不崩溃。
- 任何不确定/材料不足项写 `pending_human`，等待人工确认，不擅自补齐。

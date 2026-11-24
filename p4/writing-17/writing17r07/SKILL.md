---
name: mindmap-academic-writing
description: 把科研材料（论文、综述、实验方案、项目计划、会议纪要、知识体系）抽取为可编辑、可预览、可逐条溯源的思维导图；交付 FreeMind(.mm) 可编辑导图、SVG 预览、Markdown 大纲、evidence.json 溯源映射与 result.json。当任务要求围绕给定科研材料产出思维导图/结构化知识图并保留来源时使用。
version: 1.0.0
---

# 科研思维导图 Skill（可编辑、可复核、留来源）

## 1. 角色与何时使用

本 Skill 将一类科研材料整理成**可打开、可继续编辑、逐条可回溯**的思维导图。适用：任务明确要求围绕科研材料产出思维导图、知识结构图、研究路线图、项目推进图、会议决策图或结构化大纲。不适用：纯聊天问答、一次性制图、无来源的头脑风暴——材料不足时应如实返回 `insufficient_evidence`，不编造章节。

核心纪律：**先落盘合法骨架，再增量改进**；关键节点与关系必须挂 `locator` + 逐字 `quote`；区分事实(fact)、概括(summary)、推断(inference)；不新增材料外事实，不伪造数据、引用或软件状态。

## 2. 输入契约

运行时固定输入：
- `/input/case.json` —— 含 `case_id` 与题目 `output_contract`（字段名可能是 `output_contract`/`outputContract`/`output_schema`/`contract` 等；可能是 dict 或 list，脚本自动发现并**原样回填全部键**）。
- `/input/task.md` —— 任务说明、受众、体裁、格式与评分要求。
- `/input/` 下的材料（`materials/`、`*.pdf`、`*.md`、`*.txt`、`*.json`、`*.csv` 等）。**读实际文件**，不依据文件标签假设内容；PDF 用平台 pdf-text-tool / `pdftotext` 提取原文后按页码段落定位。

工作目录 `/app/work`，最终产物写 `/app/output/`。脚本默认参数已指向这三处，从 Skill 根目录（安装后为 `/skills/weekly-skill`）以相对路径调用。

## 3. 输出契约

必须产出 `/app/output/result.json`，包含：
1. `case_id`；
2. 题目 `output_contract` 的**全部字段**（原样回填，不改名、不删未知键，无法赋值的给 `null`/`[]`）；
3. 标准字段：`status`（`ok | partial | insufficient_evidence | input_error`；`answered` 视为 `ok` 的同义）、`sources`、`uncertainty`、`change_log`，以及 `limitations`、`pending_human`、`errors`、`degradations`、`artifacts`、`runtime`。

主产物（由 `scripts/build_map.py` 确定性渲染）：
- `mindmap.mm` —— FreeMind 1.0.1 XML，XMind/FreeMind 可继续编辑（节点带 kind/证据 NOTE，关系为 arrowlink）。
- `mindmap.svg` —— 分层树形预览，浏览器可看、矢量可改、文字不转曲。
- `outline.md` —— 带 `⟦locator⟧` 与 quote 的大纲，人类可读可改。
- `evidence.json` —— 节点/关系 → locator + quote + evidence_class 的溯源映射。
- 若环境有栅格化工具（rsvg-convert/inkscape/chromium headless/ImageMagick），另产出 `mindmap.png` 预览；否则如实写入 `limitations`。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

**第 1 步｜落盘骨架（前 3 分钟内必须完成，约 60–90 秒）**
运行 `python3 -B scripts/scaffold.py --input /input --output /app/output --work /app/work`。
输入：`/input`。输出：`/app/output/result.json`（partial 骨架）、`/app/work/discovery.json`、`/app/work/map_spec.skeleton.json`。
读脚本输出，记录 `case_id`、`contract_keys`、材料清单。此步保证中途任何失败也有合法产物。

**第 2 步｜读材料（约 60–180 秒，1–4 次请求）**
读 `/input/task.md`、`/input/case.json` 原文；按 discovery 清单遍历材料；PDF 提取文本并标注 locator（如 `materials/paper.pdf#p3`）；冲突信息双侧保留。

**第 3 步｜抽取与成稿（约 180–300 秒，最耗请求，控制在 6–12 次）**
按 `references/schema_and_rules.md` 抽取：根节点+层级（理得清）、节点 kind（抓得准）、关系 type 与方向（连得对）、每条证据 locator+quote+class（可追溯）。写入 `/app/work/map_spec.json`（以 skeleton 为模板）。此步分批写、每批落盘，避免长回复丢内容。

**第 4 步｜渲染（约 30–60 秒，确定性，0 次请求）**
运行 `python3 -B scripts/build_map.py --spec /app/work/map_spec.json --output /app/output --work /app/work`。
输入：map_spec.json。输出：主产物 + `/app/output/result.json`（契约字段已回填）+ `/app/work/artifacts.json`。

**第 5 步｜回填与诚实标注（约 30–60 秒，1–2 次请求）**
核对 result.json：设置诚实 `status`；填写 `sources`（材料清单）、`uncertainty`（冲突/推断/缺失）、`change_log`（每步动作）、`limitations`、`pending_human`。

**第 6 步｜自检与增量改进（剩余 120–240 秒，≤4 轮）**
运行 `python3 -B scripts/validate.py --output /app/output --work /app/work`；读 PASS/FAIL/INFO，修复 FAIL 后重复第 3–5 步（有限轮）；确认 result.json 合法、契约字段齐全、产物可解析后结束。

## 5. 自检清单（逐条可勾选，对应评分维度）

- [ ] 【实际效果·完成度】`mindmap.mm` 为合法 FreeMind XML；`mindmap.svg` 为合法 SVG；`outline.md`、`evidence.json` 存在且非空
- [ ] 【实际效果·正确性】节点内容忠于材料；关系方向正确、类型合法、端点存在；无材料依据的连线已删除或标 `pending_human`
- [ ] 【实际效果·完整性】覆盖关键概念/研究问题/方法/证据/结论/任务/风险/空白，且层级分支合理
- [ ] 【实际效果·可核验】关键节点与关系挂 locator+quote；数字/术语/引用可逐条回溯；推断标 `inference`
- [ ] 【使用增益】先骨架后改进，未在中途丢结果；同一输入重复运行产物一致（脚本确定性）
- [ ] 【创新增益】节点 kind 分层 + 关系方向分类 + 证据 class 三分 + SVG id 与 JSON 一一对应
- [ ] 【可靠体验】空输入/坏 JSON/坏编码/节点爆炸/写盘失败均不崩溃，退出码 0 并留痕
- [ ] 【生态贡献】`references/` 文档可复用；schema 与关系表清晰；示例标注为示意

## 6. 降级策略

- 空输入/缺 `case.json`：`scaffold.py` 仍退出 0，写 `status=insufficient_evidence`，列出缺口，不编造章节。
- 坏 JSON/坏编码：降级读取（utf-8-sig→utf-8→latin-1），记入 `errors`/`degradations`，继续处理其余材料。
- `map_spec` 缺失/为空：`build_map.py` 渲染单节点占位并写明确 `limitations`，不崩溃。
- 节点规模爆炸：截断到 400 节点并在 `degradations` 记录。
- 缺软件/缺渲染能力：如实写 `limitations`，降级用已有标准库能力（SVG 始终可交付）；**绝不声称存在未安装的软件**。
- 写盘失败：保留已写产物，写最小合法 result.json，进程不崩溃。
- 任何不确定/材料不足项写 `pending_human`，等待人工确认，不擅自补齐。

---
name: research-frontier-trend
description: 面向“研究前沿与趋势分析”的可复核综述 Skill。以“评分维度对齐”为组织原则，把题目五个评分维度（实际效果/使用增益/创新增益/可靠体验/生态贡献）逐条映射到流程步骤与自检项；用可核对的年度分布支撑趋势、用术语首末年份与归并规则追踪主题演化、按 kind（origin/method/milestone/benchmark）跨阶段选取代表作、用排除证据区分研究空白与检索遗漏；每条结论回链 locator 与逐字 quote，并显式区分材料事实、合理推断与无法确定。用于文献综述、趋势分析、主题演化追踪、研究空白识别与选题评估；不用于一次性问答、纯润色或单篇论文审稿。
version: 1.1.0
---

# 研究前沿与趋势分析 Skill（评分维度对齐版）

## 1. 角色与何时使用
本 Skill 让 Agent 把“这个方向现在做到哪、往哪走、哪里还空着”变成可复核的四类产物（时间趋势、主题演化、代表性工作、研究空白），而不是“检索前 N 条当前沿、关键词热度当趋势、换说法当新主题、总被引当代表作、没检索到当空白”。
触发条件：任务要求文献综述 / 研究前沿 / 趋势分析 / 主题演化 / 选题评估 / 研究空白识别。不适用：一次性脚本、单篇论文审稿、纯润色。

四条硬约束（对应“实际效果”40 分）：
1. 趋势必须由可核对的年度分布支撑，禁止用关键词热度代替趋势。
2. 演化必须说明“何时、因何变化”（出现/合并/分裂/衰退/术语漂移），禁止把“换了说法”当新兴主题。
3. 代表作必须覆盖各阶段且按 kind 说明口径，禁止只用总被引排序。
4. 空白必须给出排除证据（检索范围 + 纳排规则 + 反例排查），禁止把“没检索到”当空白。

## 2. 评分维度对齐（本版核心设计）
本版不把评分维度当文案，而是逐条映射到流程与自检项，并在 `result.json.alignment` 记录可核验证据。完整表格见 references/score-map.md；骨架如下：

| 维度 | 分值 | 流程落点 | 自检项 |
|-|-|-|-|
| 实际效果 | 40 | 第 5 步四类深度分析 + 第 6 步证据回链 | S1–S5 |
| 使用增益 | 25 | 第 1–2 步机械基线（去重复检索/补年份/统计）+ 第 8 步对照记录 | S6–S8 |
| 创新增益 | 15 | 年份归一、时间窗划分、术语归并、kind 选取、空白判据 | S9–S11 |
| 可靠体验 | 10 | 第 1 步先落盘骨架 + 第 8 步降级 | S12–S14 |
| 生态贡献 | 10 | scripts/、references/、可复算 CSV | S15–S16 |

每个流程步骤标维度标签（如 [A1][U2]），每条自检项也标标签；selftest.py 据此自动校验 alignment。

## 3. 输入契约（运行时）
- 原始输入在 `/input/case.json` 与 `/input/task.md`；科研材料（论文 PDF、元数据、正文、摘要、检索结果集）在 `/input/`（常见子目录 `materials/`）或由 case.json 指定路径。
- 先读实际文件内容，不依据文件名/领域标签臆测；扫描 `/input/` 全目录，不假设固定文件名。PDF 用 pdftotext（scripts/main.py 内置调用）或平台 pdf-text-tool 解析；失败按第 8 节降级。
- 文本按 utf-8-sig 优先读取，坏编码/BOM/坏 JSON 自动降级（gbk/latin-1）；单文件 >2MB 跳过并记 degradations，不崩溃。
- 工作目录 `/app/work`（中间产物）；最终产物写到 `/app/output/`，`result.json` 必产。
- 脚本调用用相对路径 `scripts/main.py`、`scripts/selftest.py`（相对 Skill 根目录 `/skills/weekly-skill`）；若当前目录不是 Skill 根目录，先 `cd /skills/weekly-skill` 再执行，或直接用绝对路径 `/skills/weekly-skill/scripts/main.py`。

## 4. 输出契约
必须产出 `/app/output/result.json`（UTF-8、合法 JSON）。若 `/input/case.json` 含 `output_contract`：原样回填其全部键（缺值给 null/[]，不改名、不删未知键），再合并固定字段；无 contract 时仅用固定字段。同时产出可编辑 `report.md`、`evidence_map.json` 与可复算 `tables/documents.csv`、`tables/year_distribution.csv`。

固定字段（字段字典与枚举见 references/score-map.md）：
- `case_id` / `status`(`ok|partial|insufficient_evidence|input_error`) / `research_question{question,scope,sub_questions[],domain_note}` / `protocol{inclusion_criteria[],exclusion_criteria[],search_scope,date_definition_note}`
- `time_trend{unit_note,distribution[],windows[],claims[]}` / `topic_evolution{stage_divisions[],term_mapping[],events[],claims[]}` / `representative_works{criteria[],stages[]}` / `research_gaps{gaps[],excluded_not_gaps[]}`
- `evidence_map[{id(E001起),claim,locator,quote,source_file,status,confidence}]` / `conflicts{items[],resolution}` / `sources` / `uncertainty[]` / `pending_human[]` / `change_log[]` / `limitations[]` / `errors[]` / `degradations[]` / `runtime` / `machine` / `alignment`

## 5. 执行流程与预算（总 900 秒、64 次模型请求、16384 tokens/响应）
先读 SKILL.md 全文，再按步骤执行；每步标维度标签。**前 3 分钟内必须先落盘一版 result.json 骨架**，之后只做增量改进；任何中断都不留空白产物。

1. 发现与落骨架（0–180s，≤4 请求）[R2][U2]：`python3 scripts/main.py`，扫描 `/input/`、解析 case.json/task.md、抽取材料文本到 `/app/work/materials/`、回填 output_contract、写 manifest.json 与 result.json 骨架（status=partial）。产出：可交付 partial 结果 + 材料清单。
2. 机械基线（180–360s，≤6 请求）[U1][I1]：main.py 已确定性抽取每篇文档年份/标题/被引、年度分布、术语首末年份、空白/冲突候选句、代表作候选，落 tables/*.csv 与 machine 候选（标 machine=true）。
3. 四类深度分析（360–720s，约 40 请求）[A1][A2][A3]：按 `time_trend → topic_evolution → representative_works → research_gaps` 顺序，逐篇读材料相关段落，把 machine 候选核验为结论（更新 support/status/confidence，去 machine 标记或注明未核验）。每完成一类即重写 result.json 并记 change_log。空白项必须写 exclusion_evidence（检索式 + 排除条件），区分“证据排除的空白”与“检索遗漏”。
4. 自检（720–820s，≤8 请求）[A4]：`python3 scripts/selftest.py`，校验契约字段、枚举、四类产物齐全、quote 可定位、无伪造分数、alignment 各维度有据；修复结构错误，无法定位的 quote 改为精确引用或置 status=not_reported 并写入 uncertainty。
5. 收尾（820–900s，≤6 请求）[R2][R3]：定稿 status/uncertainty/pending_human/limitations/change_log，复跑 selftest 确认通过；status=ok 仅在四类产物齐全且关键 claim 均有证据回链时给出。

预算纪律：前 3 分钟只保证骨架；随后按类别增量；最后 2 分钟只校验与修复，不新增大范围检查。

## 6. 四类产物质量门槛（对应“实际效果”）
1. time_trend[A1][A2]：先确认每个 doc 的年份来源（metadata 优先，其次题录/DOI/PDF 页眉），再统计；口径写于 protocol.date_definition_note 与 time_trend.unit_note（在线发表/见刊/预印本）；区分“离散波动”与“真实拐点”，拐点需相邻年份计数支撑。
2. topic_evolution[A1][A2]：term_mapping 做人工归并（同义/变体），再判 emergence/merge/split/decline/terminology_drift；每类事件写 cause 与证据。
3. representative_works[A1][A3]：按阶段分桶，每阶段按 kind（origin/method/milestone/benchmark）覆盖，避免只取高被引，覆盖长尾方法首创。
4. research_gaps[A1][A2]：每个空白写检索式 + 排除条件 + 反例排查；无法排除写 coverage_absence，可排除写 open_question；方法失效写 method_failure，证据冲突未决写 unresolved_conflict。

## 7. 证据回链与结论边界
- 每条趋势判断/演化结论/空白主张的 claims[] 以 evidence_ids[] 指向 evidence_map；evidence_map 每条含 locator（如 `materials/mat_001.txt#L12-L15`）+ 逐字 quote。
- 三类显式区分：直接可引写 support=reported；需归纳写 inferred 并在 rationale 说明；查不到写 not_reported，不补写。
- 冲突证据两方都记录并标 source_conflict，在 conflicts 里给分辨率或“未决”，不静默择一。

## 8. 自检清单（逐条勾选，标签对应评分维度，selftest.py 自动校验）
- [ ] S1[A1] result.json 合法且含 case_id、output_contract 全部字段与固定字段；四类产物均非空。
- [ ] S2[A2] 年份口径写在 date_definition_note 与 unit_note；distribution 有年度计数；每个 claim 有 evidence_ids。
- [ ] S3[A3] 时间跨度、主题层级、各阶段代表作、空白类型覆盖齐全。
- [ ] S4[A4] evidence_map 每条 quote 可定位且逐字；selftest 无“未定位”硬错误。
- [ ] S5[A2] 代表作 criteria 含 kind 口径，不只被引排序；空白项均写 exclusion_evidence。
- [ ] S6[U1] machine 基线在深度分析前已生成，减少重复检索/补年份/统计。
- [ ] S7[U2] 每条 claim 由机器候选核验而来（change_log 记录），不凭空新增。
- [ ] S8[U3] runtime/change_log 记录每步耗时与请求数，可对照基础 Agent。
- [ ] S9[I1] 时间窗划分/术语首末年份/kind 选取有方法说明（method_notes）。
- [ ] S10[I2] 年份归一、术语归并、空白判据的规则写入 protocol/term_mapping。
- [ ] S11[I3] excluded_not_gaps 与 coverage_absence 说明“不是检索前 N 条/不是没检索到”。
- [ ] S12[R1] SKILL.md frontmatter 齐备，调用路径为相对路径，可独立运行。
- [ ] S13[R2] 重复运行结果一致（确定性，无随机数）；先落盘骨架再增量。
- [ ] S14[R3] 空输入/坏 JSON/年份缺失/文献爆炸/冲突/PDF 不可读均如实标 limitations/degradations。
- [ ] S15[C1] 使用说明、依赖（仅标准库）、时间窗约定、边界齐全。
- [ ] S16[C2] 脚本/词表/统计表可复用；无编造分数、无预置答案。

## 9. 降级与失败处理
- 检索为空/缺材料：status=insufficient_evidence，research_gaps 写“材料不足，无法区分空白与遗漏”，不编造。
- 年份缺失：distribution 置空、unit_note 写“无出版年份元数据，待人工”，pending_human 记录；不猜年份。
- 主题严重歧义/同名异义：term_mapping 记录全部变体，uncertainty 说明归并口径，confidence=low。
- 文献量爆炸：按 2MB/200 文件上限采样并记 degradations；报告写明“样本边界”，不宣称全领域覆盖。
- 证据冲突/权限不足/PDF 不可读：标 source_conflict/unchecked/degradations，写入 limitations，不静默择一、不伪造文本层。
- 脚本失败：selftest 报错优先手工修复；无法修复则保留已落盘骨架并如实标 status/limitations。任何情况不伪造软件状态、数据、文献或分数；partial 结果必须标明边界。

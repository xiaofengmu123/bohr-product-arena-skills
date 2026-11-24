---
name: evidence-driven-writing
description: 文献证据驱动写作 Skill。把科研稿件拆成可判定的论断单元，为每条论断匹配来源与逐字引文、判定证据强度并校准不确定性表述，产出"论断清单—证据表—成稿"三段可复核交付；全程沉淀结构化中间产物（plan.json / claim_units.json / evidence_map.json），并用脚本对引文做逐字回验。用于学术写作中需保证事实与来源一致、逐条可回溯、证据强度相称、不确定性如实标注的任务；需指回原文位置、不得虚构缺陷时使用。
version: 1.0.0
---

# 文献证据驱动写作（evidence-driven-writing）

## 1. 角色与何时使用
本 Skill 面向"每句有依据的话都能被指认依据在哪里、依据有多强"。交付论断清单、证据表和与证据强度相称的成稿，而不是无出处的流畅改写。
适用：依据固定科研材料完成生成、改写、校对或结构化写作；需要事实、数字、引用、图表逐条回溯；需要区分证据强弱并控制表述口径。
不适用（如实说明并退出）：纯润色/翻译且明确不要求来源核验；无任何材料正文；要求整篇重写而不要求证据链。

## 2. 输入契约
运行时原始输入固定在 `/input/`：
- `/input/case.json`：任务说明、材料路径或内嵌内容、输入约束、`output_contract`（或 `outputContract`/`contract`/`result_schema`）、评分证据范围；
- `/input/task.md`：题目要求；
- 其他材料（稿件 PDF/txt/md、图表、数据）按 case.json 提供；PDF 只有原文，没有预解析答案层。
工作目录 `/app/work/`；最终产物放 `/app/output/`。
铁律：以实际读到的文件内容为准，不按标签或文件名补造内容；材料缺失、冲突、异常分别记录，绝不编造来源与数据。

## 3. 输出契约
必须产出 `/app/output/result.json`：
- `case_id`：取自 case.json（`case_id` > `caseId` > `id`）。
- 题目 `output_contract` 全部字段：优先回显 case.json 中 `output_contract`/`outputContract`/`contract`/`result_schema` 的每个键；缺失时按默认三件套补齐：`deliverable`（目标产物）、`sources`（事实与来源映射）、`change_log`（版本与边界）。
- 必须再加：`status`、`sources`、`uncertainty`、`pending_human`、`change_log`；强烈建议 `limitations`、`errors`、`degradations`、`runtime`。

字段语义：
- `status`：`complete` | `partial` | `failed` | `insufficient_evidence`；存在未核验来源或未确认项时不得写 `complete`。
- `deliverable`：主结果 = 三段交付。`claims_file`（论断清单）、`evidence_file`（证据表）、`draft`（与证据强度相称的成稿正文）及其路径。
- `sources`：逐条 `{key,value,kind,source_doc,locator,quote,support_verdict,evidence_grade,grade_reason}`；数字/术语/引用/图表/关键论断均可回溯，不新增材料外事实。
- `uncertainty`：判断不确定、证据不足、需人工确认处。
- `pending_human`：需要作者人工决策的条目（冲突证据取舍、删改取舍等）。
- `change_log`：`[{time,action,detail}]`，记录 scaffold/analyze/verify/finalize。
- `limitations` / `errors` / `degradations`：材料缺口、缺软件、截断、异常、降级记录。
locator 统一格式：`S{section}.P{para}.s{sentence}` 或 `P{para}` 或 `line{n}` 或字符区间；每条判断可指回原文复核。
另写同源可读版 `/app/output/review_report.md`（不新增事实）。

## 4. 中间产物（本 Skill 的可复核核心）
所有分析先落到 `/app/work/` 的结构化中间件，`finalize` 才合并为 result.json；任何一步失败都不丢已完成的中间件。
- `plan.json`：解析后的任务要求、体裁、受众、格式约束、材料清单、证据范围、缺口。
- `claim_units.json`：把稿件拆成可判定的最小论断单元。每条 `{claim_id, claim_text, claim_type, uncertainty_flags, status, final_wording}`。
- `evidence_map.json`：论断→来源的多对多映射。每条 `{evidence_id, claim_ids[], source_doc, locator, quote, support_verdict, evidence_grade, grade_reason}`。
- `validation_report.json`：脚本对 quote 逐字回验、locator 格式、契约字段、证据等级、表述口径的确定性检查结果。
分工：quote 是否原文可见由脚本做逐字子串校验（可复现）；"原文是否真的支持该论断"由 Agent 判定并写 `support_verdict` 与 `grade_reason`（可复核）。

## 5. 证据强度分级与表述口径
| 等级 | 定义 | 允许正文口径 |
|-|-|-|
| A 强证据 | 多篇独立原始研究一致或系统综述/荟萃分析；大样本高质量设计；领域共识 | 表明 / 证实 |
| B 中等证据 | 单篇设计良好的原始研究或指南推荐 | 研究发现 / 报告 |
| C 弱证据 | 单篇小样本、临床前、病例报告、亚组分析 | 初步表明 / 提示 |
| D 不足证据 | 专家意见、无原始数据的综述陈述、仅摘要 | 有观点认为 / 可能 |
| U 无法核验 | 元数据无法确认、原文不可得 | 不得作确定性结论，记 pending_human |
规则：相关性不写成因果；单篇不写成共识；冲突证据并列呈现，不隐藏；`verify` 对等级与措辞做确定性匹配检查（hedge_too_strong 为阻断项）。

## 6. 执行流程（900s / 64 次请求；每阶段写 partial，不等待完美）
- P0 读题+骨架（0–3 min，≤3 请求）：读 task.md + case.json + 目录清单；运行 `python scripts/main.py scaffold --out /app/output --work /app/work`。**硬性要求：前 3 分钟内必须先落盘一版 result.json 骨架（status=in_progress），此后只做增量改进，绝不因重跑而丢失。**
- P1 取料+解析（3–12 min，≤20 请求）：运行 `python scripts/main.py extract --input /input --work /app/work`；PDF 走 `pdftotext -layout`，缺软件时标准库 zlib 兜底并记 degradations。整篇通读原文，不用摘要替代。
- P2 论断切分+证据匹配（12–13.5 min，≤25 请求）：运行 `python scripts/main.py claims --work /app/work` 生成论断骨架，Agent 逐条填写 claim_units.json；再运行 `python scripts/main.py evidence --work /app/work` 生成证据表骨架并填写 evidence_map.json。每条论断：quote 逐字、support_verdict、evidence_grade、grade_reason 四件齐全；一条论断可多来源，一篇文献可支撑多条论断。
- P3 校验+组装（13.5–15 min，≤10 请求）：运行 `python scripts/main.py verify --work /app/work --out /app/output`，再 `python scripts/main.py finalize --work /app/work --out /app/output`。校验字段、回显契约键、逐字回验 quote、补 change_log。
- 预留 ~6 请求 / 60s 处理重试与异常；任一步失败先落盘 partial 并标注。
脚本从 Skill 根目录以相对路径执行（`scripts/xxx.py`）；若 cwd 不是 Skill 根，用 `/skills/weekly-skill/scripts/xxx.py`。

## 7. 自检清单（对应评分维度）
实际效果
- [ ] 三段交付齐全：论断清单、证据表、成稿正文存在且互相链接（claim_id↔evidence_id）
- [ ] 每条实质论断定位到来源（source_doc+locator+quote），quote 与原文逐字一致（脚本已回验）
- [ ] 引用真实存在、著录准确；原文真的支持该论断（support_verdict 非 no_support/contradicts 且 grade_reason 说明支持范围）
- [ ] 证据等级与正文表述相称（verify 无 hedge_too_strong）
- [ ] 冲突证据、机制未明、适用边界用 uncertainty_flags 标注，未隐藏
使用增益
- [ ] 同稿同要求下比"通用建议"多给出 claim 粒度来源映射与逐字引文，减少回原文核对
- [ ] 中间产物可独立复用：他人可用 plan/claim_units/evidence_map 重跑 verify 与 finalize
创新增益
- [ ] 含结构化中间表示（plan/claim_units/evidence_map）与脚本化 quote 逐字回验（确定性、可复现）
- [ ] 证据强度分级→表述口径映射与不确定性标记，避免弱证据写成强结论
可靠体验
- [ ] 缺章节/无参考文献/图表公式缺失/格式混乱/截断均识别并写入 limitations
- [ ] 重复运行不覆盖已改进结果（scaffold 不覆盖非 in_progress 版本）；同输入两次运行结果一致（确定性脚本）
生态贡献
- [ ] SKILL.md、scripts、references 齐备；依赖/路径/限制写清；最小示例标注为示例

## 8. 降级策略
- 缺稿件正文：status=partial，只写已核验部分，limitations 写明缺口；不得补造正文。
- 缺软件（无 pdftotext）：用标准库 zlib 兜底；仍失败则 degradations 记录，status=partial/failed。
- 材料冲突/歧义：保留多种解读写入 uncertainty 与 pending_human，标注待人工确认。
- 图表/公式缺失或不可读：不猜内容，记 limitations；可描述"该处有图/公式但无法读取"。
- 数字/引用/术语无法回溯：不写入 deliverable；确需保留则记 uncertainty 并降级为 pending_human。
- 坏 JSON/坏编码/空目录：捕获并记录 errors/degradations，继续处理其余材料；空输入返回 status=insufficient_evidence。
- 严禁编造评测分数、运行结果、基准对比或隐藏测试；结果只来自本次实际运行。

## 9. 最小调用示例（示例，非正式案例）
以下为接口示意，非正式案例结果；示意输出须以本例为准，不得冒充正式测试。
```
# 输入目录结构（示例）
/input/case.json          # {"case_id":"demo-001","output_contract":{...}}
/input/task.md
/input/draft.md           # 待核验底稿
/input/paper.pdf          # 支撑文献

# 调用序列（从 Skill 根目录执行）
python scripts/main.py scaffold --out /app/output --work /app/work
python scripts/main.py extract  --input /input --work /app/work
python scripts/main.py claims   --work /app/work            # 生成论断骨架
python scripts/main.py evidence --work /app/work            # 生成证据表骨架
# Agent 填写 claim_units.json 与 evidence_map.json（语义判定）
python scripts/main.py verify   --work /app/work --out /app/output
python scripts/main.py finalize --work /app/work --out /app/output
```
产物（示例）：
- `/app/work/plan.json`、`claim_units.json`、`evidence_map.json`、`validation_report.json`
- `/app/output/result.json`（`case_id`+契约字段+`status/sources/uncertainty/pending_human/change_log`）
- `/app/output/review_report.md`、`/app/output/draft.md`
自检：`python scripts/selftest.py` 退出码 0。

## 10. 边界
本 Skill 只解决"文献证据驱动写作"这一件事：把写作产出与来源/证据强度绑定。不做通用大而全写作系统；没有真实材料时输出"材料不足"，不用示例填充。提交前在非作者环境跑一遍 `python scripts/selftest.py`（退出码 0 为通过）。

---
name: evidence-driven-writing
description: 文献证据驱动写作 Skill。把科研写作任务落到"论断清单—证据表—成稿"三段交付：从全文提取可判定论断、逐条关联到材料具体段落（locator + 逐字 quote）、按研究设计/样本量/重复验证/共识程度给证据强度分级（strong/moderate/weak/insufficient），并在结论边界处加恰当的不确定性限定语（相关性≠因果、单篇小样本≠共识、冲突证据不隐藏），产出成稿与可逐条回溯的来源/修改记录。用于学术写作、综述、报告、改稿、引用核验等要求"每一句有依据的话都能指认依据与强度"的任务。
version: 1.0.0
---

# 文献证据驱动写作（evidence-driven-writing）

## 1. 角色与何时使用
本 Skill 面向"有依据的写作"，核心不是让模型多写，而是让成稿里的每个实质论断都能被指认**依据在哪里、依据有多强**。交付物是三段式：论断清单（claims）、证据表（sources）、成稿（deliverable），外加不确定性标注与可回查记录。

适用：需要文献支撑的学术写作、综述、报告、改写、校对、引用核验；要求区分原始研究/综述/指南/预印本、处理一条论断多篇文献与一篇文献支撑多条论断、防止把弱证据写成强结论的场景。

不适用（如实说明并退出）：无任何材料正文；仅需格式排版且明确不要求证据溯源；要求输出不含来源的纯宣传文案。

## 2. 输入契约
运行时原始输入固定在 `/input/`：
- `/input/case.json`：任务说明、材料路径或内嵌内容、输入约束、`output_contract`（可能在 `output_contract`/`outputContract`/`contract`/`result_schema` 任一键下）与评分证据范围；
- `/input/task.md`：题目要求；
- 其他材料（论文 PDF/txt/md、图表、数据）按 case.json 提供的路径读取；PDF 只有原文，无预解析答案层。

工作目录 `/app/work/`；最终产物放 `/app/output/`。铁律：以实际读到的文件内容为准，不按文件名/标签补造内容；材料缺失、冲突、异常分别记录到 `limitations`/`uncertainty`/`errors`。

## 3. 输出契约
必须产出 `/app/output/result.json`，字段：
- `case_id`：取自 case.json（`case_id` > `caseId` > `id`），缺失时用文件名或 `unknown`。
- 题目 `output_contract` 全部字段：原样回显 case.json 中 contract 的每个键（缺值给 `null`/`[]`，不改名、不删未知键）；缺失时按默认契约 `deliverable`（目标产物）、`sources`（事实与来源映射）、`change_log`（版本与边界）补齐。
- 必须再加：`status`、`claims`、`sources`、`uncertainty`、`pending_human`、`change_log`、`limitations`；强烈建议 `errors`、`degradations`、`runtime`。

字段语义：
- `status`：`complete` | `partial` | `insufficient_evidence` | `input_error` | `failed`；材料不足不得写 `complete`。
- `deliverable`：主产物 = 成稿 + 论断清单 + 证据表，写成可编辑 `deliverable.md`（或按题目要求给 docx/其他格式，Office 产物需同时给真实 PDF 预览）。
- `claims`：论断清单。每条含 `claim_id`（C1..Cn）、`text`（可判定真假的一句话）、`claim_type`（fact/interpretation/causal/consensus/recommendation）、`evidence_strength`（strong/moderate/weak/insufficient）、`strength_reason`、`uncertainty`（应使用的限定语）、`sources`（引用到的 source_id 列表）、`needs_human`。
- `sources`：证据表。每条含 `source_id`（S1..Sn）、`source_doc`、`source_type`（original_study/review/guideline/preprint/other）、`metadata`、`locator`（统一 `S{section}.P{para}.s{sentence}`，无法分节用 `P{para}` 或页码/字符区间）、`quote`（逐字原文摘录）、`claim_ids`、`support`（supports/partially_supports/weakly_supports/conflicts）、`verification`（verified/unverified/conflict）。
- `uncertainty`：冲突证据、机制未明、样本局限、需人工确认处。
- `change_log`：`[{time,action,detail}]`，记录 scaffold/extract/match/grade/draft/finalize。
- `pending_human`：需要人工确认的条目。

locator 与 quote 是硬要求：每条 key claim 必须能指回原文逐字片段；数字、术语、引用、图表、关键论断均可回溯，不新增材料外事实。

## 4. 执行流程（900s / 64 请求；每阶段先落 partial，不等待完美）
- **P0 读题 + 骨架（0–3 min，≤3 请求）**。读 task.md + case.json + 目录清单，运行 `python scripts/scaffold.py --case /input/case.json --out /app/output --work /app/work`。**硬性要求：前 3 分钟内必须先落盘一版 result.json（status=in_progress），此后只做增量改进，绝不因重跑丢失。** scaffold 默认不覆盖非 in_progress 版本。
- **P1 取料 + 切分（3–10 min，≤20 请求）**。读全部材料正文（PDF 用 `pdftotext -layout <pdf> /app/work/materials.txt`，或调用平台 pdf-text-tool；全文通读，不用摘要替代）。把每段可判定论断拆成 claim 单元，逐条配 locator + 逐字 quote，写 `/app/work/claims.json`。
- **P2 匹配 + 强度分级（10–13 min，≤25 请求）**。为每条 claim 匹配支撑 source（一条 claim 可多源、一源可多 claim），按 references/01_evidence_framework.md 的强度口径定级，标注 support 级别与不确定性限定语，写 `/app/work/evidence.json`、`/app/work/uncertainty.json`。发现证据不足/冲突就写入 `uncertainty` 与 `pending_human`，不为凑数虚构缺陷。
- **P3 成稿 + 组装（13–14.5 min，≤10 请求）**。按题目体裁/受众写 `/app/work/deliverable.md`，正文以 `[C1]` 式标号指认论断，措辞与证据强度相称；运行 `python scripts/main.py assemble --case /input/case.json --work /app/work --out /app/output` 合并 result.json，再 `python scripts/main.py validate --out /app/output` 校验。
- 预留 ~6 请求 / 60s 处理重试；任一步失败先落盘 partial 并标注。脚本从 Skill 根目录以相对路径执行；若 cwd 不是 Skill 根，用 `/skills/weekly-skill/scripts/main.py`。

## 5. 证据强度与不确定性口径
强度定级参考 references/01_evidence_framework.md：结合研究设计、样本量、重复验证与共识程度。硬规则：相关性不写因果（claim_type 标 causal 需有因果设计证据）；单篇小样本不写领域共识；冲突证据必须并列记录、不隐藏。不确定性表述要"既不夸大也不含糊到无法判断"，逐条写入 `claims[].uncertainty` 并在成稿中落地为限定语。

## 6. 自检清单（对应评分维度）
- [ ] 三段交付齐全：论断清单（claims）+ 证据表（sources）+ 成稿（deliverable）均存在且非空。
- [ ] 每条 claim 有 locator + 逐字 quote，指回原文可独立复核；无虚构引用/DOI/数值。
- [ ] 每条 claim 有 evidence_strength + strength_reason；强/弱措辞与强度相称。
- [ ] 冲突证据与不确定性已显式记录（uncertainty/pending_human），未隐藏。
- [ ] 逐条引用了题目 output_contract 全部键，未改名未删键。
- [ ] result.json 含 case_id/status/sources/uncertainty/change_log/limitations。
- [ ] 材料缺口（缺章节/仅摘要/图表不可读/格式损坏/截断）写入 limitations。
- [ ] 重复运行结果一致；scaffold 不覆盖已改进版本。
- [ ] 交付前跑过 `python scripts/selftest.py`，全 PASS。

## 7. 降级策略
- 缺材料正文：status=insufficient_evidence，只写已验证部分，limitations 写明缺口；不得补造正文。
- 缺软件（如无 pdftotext）：用平台 pdf-text-tool 或标准库兜底；仍失败则记 limitations，status=partial/failed。
- 材料冲突/歧义：保留多种解读写入 uncertainty，标待人工确认。
- 图表/公式不可读：不猜内容，记 limitations，可写"该处有公式/图但无法读取"。
- 数字/引用/术语无法回溯：不写入 deliverable；确需保留则记 uncertainty。
- 坏 JSON/坏编码：捕获写入 errors/degradations，继续处理其余材料；空输入退出码 0 并写合法 insufficient_evidence。
- 严禁编造评测分数、运行结果、基准对比或隐藏测试；结果只来自本次实际运行。

## 8. 最小调用示例（示例，非正式案例）
输入 `/input/case.json`（示意）：`{"case_id":"case-1","output_contract":{"deliverable":null,"sources":[],"change_log":[]}}`，材料 `/input/paper.pdf`。
调用：
```bash
python scripts/scaffold.py --case /input/case.json --out /app/output --work /app/work
# P1–P3：Agent 写 /app/work/claims.json、evidence.json、deliverable.md
python scripts/main.py assemble --case /input/case.json --work /app/work --out /app/output
python scripts/main.py validate --out /app/output
```
示意输出 `result.json`（节选，仅为说明接口，非正式结果）：`{"case_id":"case-1","status":"complete","claims":[{"claim_id":"C1","text":"…","evidence_strength":"moderate","sources":["S1"]}],"sources":[{"source_id":"S1","locator":"S3.P2.s1","quote":"…"}],"change_log":[…],"uncertainty":[],"limitations":[]}`。

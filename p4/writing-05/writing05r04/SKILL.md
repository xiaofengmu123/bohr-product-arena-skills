---
name: literature-evidence-writing
description: 文献证据驱动写作 Skill。把一段需要文献支撑的写作任务落到"论断清单—证据表—成稿"三段交付：从全文（非仅摘要）抽取可定位论断，为每条论断匹配可逐字回溯的来源与支撑范围，按研究设计/样本量/重复验证/共识程度给出 S1–S4 证据强度并校准正文表述，显式记录不确定性、人工确认项与材料缺口。用于学术写作、修改、引用校验、证据强度与不确定性表达等要求"每条有依据的话都能指认依据在哪里、依据有多强"的任务；需防止虚构来源、夸大证据、隐藏冲突时使用。
version: 1.0.0
---

# 文献证据驱动写作（literature-evidence-writing）

## 1. 角色与何时使用
本 Skill 解决一个明确问题：**让成稿中每一句实质性论断都能被独立回查——指认依据在哪、依据有多强。** 核心不是让模型多写，而是把"找得到、对得上、说得准、复得现"落到可复核的三段交付。
适用：文献证据驱动写作、改写、引用/格式校验、证据强度与不确定性表达；需从材料产出可编辑稿件并保留来源/修改记录。
不适用（如实说明并退出）：纯语言润色且不要求证据溯源；无任何材料正文；明确要求不保留来源的创作类文本。

## 2. 输入契约
原始输入固定在 `/input/`：
- `/input/case.json`：任务说明、材料路径或内嵌内容、输入约束、`output_contract`/`outputContract`/`contract`/`result_schema` 字段、评分证据范围；
- `/input/task.md`：题目要求；
- 其他材料（PDF/txt/md/json/csv、图表、数据）按 case.json 提供；PDF 只有原文，无预解析答案层。
工作目录 `/app/work/`；最终产物放 `/app/output/`。
铁律：**以实际读到的文件内容为准**，不按标签/文件名补造内容；材料缺失、冲突、异常分别记录，不静默绕过。

## 3. 输出契约
必须产出 `/app/output/result.json`，至少包含：
- `case_id`：取自 case.json（`case_id` > `caseId` > `id` > `armiId`）。
- 题目 `output_contract` 全部字段：优先回显 case.json 中上述契约键的每个键（缺值给 `null`/`[]`，不改名、不删未知键）；缺失时按默认契约 `deliverable`（目标产物）、`sources`（事实与来源映射）、`change_log`（版本与边界）补齐。
- 必须再加：`status`、`sources`、`uncertainty`、`pending_human`、`change_log`；强烈建议 `limitations` 与 `runtime`。

字段语义：
- `status`：`complete`|`partial`|`failed`（骨架阶段为 `in_progress`）；缺数据不得写 `complete`。
- `deliverable`：主结果 = 三段交付。`claims`（论断清单，每条 `claim_id`/`claim_text`/`source_ids`/`wording_grade`/`verdict`）+ `evidence_table` + 成稿正文 + `summary`；另写同源可读版 `/app/output/evidence_writing.md`。
- `sources`：逐条 `{id,key,value,kind,source_doc,locator,quote,support_scope,strength,design,sample_size,change}`；数字/术语/引用/图表/关键论断均可回溯，不新增材料外事实。
- `uncertainty`：`[{id,claim_id,issue,resolution?}]`；`pending_human`：`[{id,item,why}]`；`limitations`：`[{id,kind,detail,impact}]`；`change_log`：`[{time,action,detail}]`，记录 scaffold/discover/extract/strength/write/finalize/selftest。

locator 统一格式 `S{section}.P{para}.s{sentence}`；无法分节用 `P{para}` 或字符区间。每条判断可指回原文复核（详见 `references/02_checklist.md`）。

## 4. 执行流程（900s / 64 请求；每阶段先写 partial，不等待完美）
- **P0 读题+骨架（0–3 min，≤3 请求）**：读 task.md + case.json + 目录清单；运行 `python scripts/scaffold.py`。**硬性要求：前 3 分钟内必须先落盘一版 result.json（status=in_progress），此后只做增量改进，绝不因重跑丢失已改进结果。** 脚本默认不覆盖已 finalize（非 in_progress）的版本。
- **P1 取料+定位（3–9 min，≤20 请求）**：运行 `python scripts/evidence.py`，材料归一为 `/app/work/materials/*.txt`，按 locator 切分句子，抽取数字/引用等证据原子，写 `/app/work/analysis.json` 与 `/app/work/evidence_template.json`，并落盘 `/app/output/evidence_writing.md` 三段模板。PDF 用 `pdftotext -layout`；缺失则记录降级。整篇通读原文，不用摘要替代。
- **P2 论断切分+定级（9–12.5 min，≤25 请求）**：基于 analysis.json + 原文，逐句判定可判定论断，匹配来源与支撑范围，按 `references/01_evidence_strength.md` 给 S1–S4 强度并校准成稿表述；冲突、机制未明、外推、缺样本量等写 uncertainty。无据可循的论断：改写弱化 / 删除 / 挂 pending_human，不静默保留。
- **P3 组装+自检（12.5–15 min，≤10 请求）**：把三段交付写入 result.json，置 status 终态；运行 `python scripts/selftest.py --render` 校验契约、交叉核对论断↔来源、渲染证据附录。预留 ~6 请求 / 60s 处理重试与异常；任一步失败先落盘 partial 并标注 limitations。

脚本从 Skill 根以相对路径执行；若 cwd 不是 Skill 根，用 `/skills/weekly-skill/scripts/evidence.py`。

## 5. 自检清单（逐条可勾选，对应评分维度；`scripts/selftest.py` 自动执行结构项）
实际效果
- [ ] 三段交付齐全：论断清单 / 证据表 / 成稿；每条论断可独立判定真伪。
- [ ] 引用真实存在，元数据与原文一致，原文确支持该论断（非相近但更弱/更强的话）。
- [ ] 论断、来源、证据强度标注、不确定性表述四类要素齐备。
- [ ] 每条来源含 source_doc + locator + 逐字 quote，可独立回查。

使用增益
- [ ] 相比"仅给成稿"额外提供证据表与论断编号，减少回原文核对返工。
- [ ] 过度推断段落已弱化或删除；冲突证据未隐藏。
- [ ] 同稿同口径记录在 sources/change_log，可支撑增益核验。

创新增益
- [ ] 含论断切分 + 来源匹配 + S1–S4 强度定级 + 表述校准的完整方法链（基础 Agent 难以稳定实现）。
- [ ] 分级口径、support_scope、降级分类可独立复用（references/01_evidence_strength.md）。
- [ ] 创新边界写明：只做证据支撑质量，不做自动文献检索/DOI 补全。

可靠体验
- [ ] 前 3 分钟落盘骨架；重复运行不覆盖已 finalize 版本；缺材料/坏编码/坏 JSON 不崩溃。
- [ ] 缺章节/无参考文献/图表公式缺失/格式混乱/截断/权限不足均写入 limitations。
- [ ] 原文与摘要矛盾、证据冲突、二手转引、撤稿/版本差异均写入 uncertainty。

生态贡献
- [ ] SKILL.md、scripts、references 齐备；依赖（仅 Python 标准库 + 可选 pdftotext）与路径写清；最小示例标注为示例（见 references/02_checklist.md）。

## 6. 降级策略（缺数据/缺软件如实标注，不编造）
- 缺稿件正文/材料缺失：`status=partial`，只写已验证部分，`limitations` 写明缺口；不补造正文。
- 缺软件（如无 pdftotext）：evidence.py 记录 `pdftotext_missing` 并跳过该 PDF；仍失败则 limitations 记录，status=partial/failed。
- 仅有摘要/原文不可得：标"仅摘要"，相关论断降为 S4 或挂 pending_human。
- 原文与摘要矛盾、证据冲突：双方并列记录，标 source_conflict，写 uncertainty，待人工裁决。
- 图表/公式缺失或不可读：不猜内容，记"该处有公式/图但无法读取"。
- 数字/引用/术语无法回溯：不写入 deliverable；确需保留则记 pending_human。
- 坏 JSON/坏编码/文件超限：捕获并记录到 `_meta.discovery_errors` 或 limitations，继续处理其余材料。
- 严禁编造评测分数、运行结果、基准对比或隐藏测试；结果只来自本次实际运行。

## 7. 文件清单与依赖
```
SKILL.md                 # 本文件（frontmatter: name/description/version）
scripts/scaffold.py      # P0 骨架 + 输入发现 + 契约回显（幂等）
scripts/evidence.py      # P1/P2 材料归一 + 定位切分 + 证据原子 + 模板
scripts/selftest.py      # P3 自检 + 交叉核对 + 渲染证据附录
references/01_evidence_strength.md   # 强度分级 S1–S4 / support_scope / 表述校准
references/02_checklist.md           # locator 规范 / 自检清单 / 最小示例
```
依赖：仅 Python 3 标准库；PDF 文本抽取可选依赖 `pdftotext`（缺则降级）。脚本入口均以相对路径 `scripts/evidence.py` 调用，支持 `--input/--output/--work` 覆盖缺省 `/input`、`/app/output`、`/app/work`。

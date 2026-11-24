---
name: evidence-driven-writing
description: 文献证据驱动写作 Skill。把科研材料拆成可判定的论断单元，为每条论断匹配到具体来源段落（locator+逐字 quote），按研究设计与共识程度给出证据强度等级，据此约束成稿措辞（相关性≠因果、单篇小样本≠领域共识、冲突不隐藏），产出"论断清单—证据表—成稿"三段可复核交付，并对材料不足/冲突/需人工确认处如实标注。用于需要事实、数字、引用可逐条回溯的学术写作、改稿、校对、引用核验与结构化任务。运行时输入 /input/case.json 与 /input/task.md，产物写 /app/output/result.json。鲁棒性优先：空输入、坏 JSON、缺参数、超大文件均优雅降级不崩溃。
version: 1.1.0
---

# 文献证据驱动写作（evidence-driven-writing）

## 1. 角色与何时使用

本 Skill 解决"写出来的每句有依据的话都能被指认依据在哪、依据有多强"这一核心问题，交付**论断清单 → 证据表 → 成稿**三段式、可独立复核的产物，而不是一份看起来完整却无法回溯的成稿。

适用：需要文献支撑的写作/改写/校对/引用核验/结构化任务；数字、术语、引用、图表、关键论断必须逐条回到原文的任务；要求区分证据强弱与不确定性表达的任务。

不适用（如实说明并退出）：纯语言润色且明确不要证据溯源；无任何材料正文；要求输出整篇重写而非"论断—证据—成稿"方案。

四个硬指标，贯穿全程：
- **找得到**：每条实质性论断都能关联到具体文献的具体段落（locator + 逐字 quote）。
- **对得上**：文献真实存在、元数据准确、原文真的支持**这句**论断，而不是一句更弱或更强的话。
- **说得准**：证据强度与不确定性如实表达；相关≠因果，单篇≠共识，冲突不隐藏。
- **复得现**：论断清单、证据表、原文摘录与判定理由能被第三方独立回查与重跑。

## 2. 输入契约

运行时原始输入固定在 `/input/`：
- `/input/case.json`：任务说明、材料路径或内嵌内容、输入约束、可能的 `output_contract` 字段、评分证据范围；
- `/input/task.md`：题目要求；
- 其他材料（PDF/txt/md/图表/数据）按 case.json 提供；PDF 只有原文，没有预解析答案层。

工作目录 `/app/work/`；最终产物放 `/app/output/`。Skill 被安装为 `/skills/weekly-skill`，脚本从 Skill 根以相对路径调用：`python scripts/main.py ...`；若 cwd 不是 Skill 根，用 `/skills/weekly-skill/scripts/main.py`。

铁律：以实际读到的文件内容为准，不按标签/文件名补造内容；材料缺失、冲突、异常分别记录。

## 3. 输出契约

必须产出 `/app/output/result.json`（同源可读版 `/app/output/evidence_report.md`，不新增事实）：

- `case_id`：取自 case.json（`case_id` > `caseId` > `id`）。
- 题目 `output_contract` 的全部字段：优先回显 case.json 中 `output_contract`/`outputContract`/`contract`/`result_schema` 的每个键（缺值给 `null`/`[]`，不改名不删键）；缺失时按默认契约 `deliverable`（目标产物）、`sources`（事实与来源映射）、`change_log`（版本与边界）补齐。
- 必须再加：`status`、`sources`、`uncertainty`、`change_log`；强烈建议 `pending_human`、`limitations`、`errors`、`degradations`、`runtime`。

字段语义：
- `status`：`complete`|`partial`|`insufficient_evidence`|`input_error`|`failed`；缺数据不得写 `complete`。
- `deliverable`：主结果 = `claims`（论断清单）+ `evidence_table`（证据表）+ `manuscript`（成稿路径）+ `summary`（摘要）。每条论断含 `claim_id`、`claim`、`claim_type`、`locator`、`quote`；每行证据含 `claim_id`、`source_doc`、`locator`、`quote`、`strength`、`supports`、`rationale`。
- `sources`：逐条 `{key,value,kind,location,source_doc,quote,strength}`；数字/术语/引用/图表/关键论断均可回溯，不新增材料外事实。
- `uncertainty`：判断不确定、证据不足、冲突、需人工确认处。
- `pending_human`：须由作者/人工定夺的越界、二手转引、未核验项。
- `change_log`：`[{time,action,detail}]`，至少记录 scaffold/analyze/diagnose/finalize。
- `limitations`/`degradations`/`errors`：材料缺口、缺软件、截断、坏 JSON、超大文件等异常。

locator 统一格式：`S{节}.P{段}.s{句}`；无法分节用 `P{段}.s{句}` 或字符区间 `@起-止`；每条判断可指回原文复核，凡引原文必带逐字 `quote`。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求，每响应 ≤16384 token）

每阶段先写 partial，绝不因追求完美而丢结果。**硬性要求：前 3 分钟内必须先落盘一版 `result.json` 骨架（`status=in_progress`），此后只做增量改进，重复运行不覆盖已改进内容。**

- **P0 读题 + 骨架（0–3 min，≤3 请求）**。输入 task.md + case.json + 目录清单；输出 `result.json` 骨架。运行 `python scripts/main.py scaffold`（或 `run` 的第一步）。若 case.json 缺失/坏 JSON，仍落盘骨架并把错误记入 `errors`。
- **P1 取料 + 发现（3–10 min，≤20 请求）**。运行 `python scripts/main.py discover` 产出 `/app/work/manifest.json`（含 oversized 清单）；`python scripts/main.py extract` 把 PDF 用 `pdftotext -layout` 转 `/app/work/manuscript.txt`，txt/md 原样拷贝。整篇通读原文，不用摘要替代。
- **P2 三段分析（10–13.5 min，≤25 请求）**。输入原文 + `references/01_evidence_method.md` + `references/02_strength_grading.md`；输出 `/app/work/claims.json`、`/app/work/evidence_table.json`、`/app/work/sources.json`，成稿写 `/app/output/manuscript.md`（或题目指定格式）。论断一句一断言；证据逐行带 locator+quote+strength+supports。
- **P3 组装 + 校验（13.5–15 min，≤10 请求）**。运行 `python scripts/main.py finalize` 合并工作文件、回显契约键、校验 strength 枚举、补 change_log、写 `/app/output/evidence_report.md`。运行 `python scripts/selftest.py` 做鲁棒性自检。
- 预留 ~6 请求 / 60s 处理重试与异常；任一步失败先落盘 partial 并标注，绝不重跑丢结果。

## 5. 自检清单（对应评分维度，逐条勾选）

实际效果
- [ ] 三段交付齐全：论断清单 + 证据表 + 成稿，三者同源、claim_id 可互查。
- [ ] 引用真实存在、著录准确、原文真的支持该句（`supports=yes|partial|no` 已判）。
- [ ] 四要素齐备：论断、来源、证据强度标注、不确定性表述。
- [ ] 每条可独立复核：claim_id + locator + quote + rationale 齐全，quote 与原文逐字一致。

使用增益
- [ ] 相比"通用建议"更具体：每条建议是可回溯来源、可执行的编辑动作，不是写作原则。
- [ ] 一次给全强度等级与不确定性，减少回原文核对、补引用、改过度推断的返工。
- [ ] 同稿同版本同口径记录在 sources/change_log，可支撑增益验证。

创新增益
- [ ] 含至少一项结构级产物：论断切分、论断—来源多对多映射、证据强度分级、冲突/边界比对。
- [ ] 脚本 + 强度阶梯 + locator 规范可独立复用（标准库、相对路径）。

可靠体验
- [ ] 空输入、坏 JSON、缺参数、超大文件、缺软件均优雅降级不崩溃（`python scripts/selftest.py` 全 PASS）。
- [ ] 缺章节/无参考文献/图表公式缺失/格式混乱/截断均识别并写入 limitations/degradations。
- [ ] 重复运行不覆盖已改进结果；status 诚实，缺数据不写 `complete`。

生态贡献
- [ ] SKILL.md、scripts、references 齐备；依赖（仅标准库）、路径、限制写清；最小示例标注为示例。

## 6. 降级策略（鲁棒性为本次侧重点）

- 空输入目录 / 缺 case.json：退出码 0，写合法 `result.json`（`status=insufficient_evidence` 或 `input_error`），`errors` 写明缺口，不抛异常。
- 坏 JSON / 坏编码：捕获并记入 `errors`/`degradations`，继续处理其余材料（utf-8-sig → utf-8 → gb18030 → latin-1 依次回退）。
- 缺参数 / 未知参数：`--input/--output/--work/--case/--task` 缺省指向 `/input`、`/app/output`、`/app/work` 等，永不因参数缺失崩溃。
- 超大文件：单文件 >2MB 跳过并记 `oversized`/`degradations`，不读入内存。
- 缺软件（如无 pdftotext）：用文本直读兜底；仍失败则记 `degradations`，status=partial/failed，不编造正文。
- 材料冲突/歧义：保留多种解读写入 `uncertainty`，标 `source_conflict`，待人工确认。
- 图表/公式缺失或不可读：不猜内容，记 limitations，可描述"该处有公式/图但无法读取"。
- 数字/引用/术语无法回溯来源：不写入 deliverable；确需保留则记 uncertainty/pending_human。
- 严禁编造评测分数、运行结果、基准对比或隐藏测试；结果只来自本次实际运行。

## 7. 最小调用示例（示意，非正式案例）

```bash
# 运行时（Harness 已在 /input 放好 case.json + task.md + 材料）
python scripts/main.py run                      # scaffold -> discover -> extract -> finalize
python scripts/main.py finalize                 # Agent 写入 claims/evidence/sources 后合并
python scripts/selftest.py                      # 5 个鲁棒性场景自检
```

示意 result.json（片段，标注为示例）：

```json
{"case_id": "case-demo", "status": "partial",
 "deliverable": {"claims": [{"claim_id": "C01", "claim": "示例论断", "locator": "S1.P1.s2", "quote": "原文逐字摘录"}],
 "evidence_table": [{"claim_id": "C01", "source_doc": "material.md", "locator": "S1.P1.s2", "quote": "原文", "strength": "cohort", "supports": "partial", "rationale": "观察性设计，仅相关"}],
 "manuscript": "manuscript.md", "summary": "示例摘要"}}
```

脚本只依赖 Python 标准库；`main.py < 30KB`（健壮逻辑集中在 `scripts/core_impl.py`）。

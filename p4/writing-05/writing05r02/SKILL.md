---
name: evidence-driven-writing
description: 文献证据驱动写作 Skill。把科研材料拆成可判定的论断单元，为每条论断匹配可逐字回溯的来源、证据强度与支持判定，并按证据强度控制结论表述，产出"论断清单—证据表—成稿"三段交付与可回查的 result.json。用于需保留事实、数字、引用来源并明确不确定性的学术写作/改写/引用校验任务；需指回原文、不得虚构数据或引用时使用。
version: 1.0.0
---

# 文献证据驱动写作（evidence-driven-writing）

## 1. 角色与何时使用

本 Skill 解决"写出来的每一句有依据的话，都能被指认依据在哪里、依据有多强"的问题。核心交付不是多写，而是可回溯：把材料拆成论断单元（`claims`），逐条配证据（`sources`），给出证据强度（`strength`）与支持判定（`support`），再据此成稿，使"找得到、对得上、说得准、复得现"四个目标可被独立复核。

适用：依据固定科研材料完成指定学术写作/改写/引用校验/结构化写作，要求保留事实、数字、引用来源与修改记录。
不适用（如实说明并退出）：纯语言润色且不要求证据溯源；无任何材料正文；要求输出整篇重写而不保留来源记录。

## 2. 输入契约

原始输入固定在 `/input/`：
- `/input/case.json`：任务说明、材料路径或内嵌内容、输入约束、`output_contract`/`outputContract`/`contract`/`result_schema` 字段、评分证据范围；
- `/input/task.md`：题目要求；
- 其他材料（PDF/txt/md/图表/数据）按 case.json 提供；PDF 只有原文，没有预解析答案层。

工作目录 `/app/work/`；最终产物放 `/app/output/`。
铁律：以实际读到的文件内容为准，不按标签或文件名补造内容；材料缺失、冲突、异常分别记录。

## 3. 输出契约

必须产出 `/app/output/result.json`：
- `case_id`：取自 case.json（`case_id` > `caseId` > `id` > `challenge_id`）。
- 题目 `output_contract` 全部字段：优先回显 case.json 中的 `output_contract`/`outputContract`/`contract`/`result_schema` 每个键；缺失时按默认契约 `deliverable`（目标产物）、`sources`（事实与来源映射）、`change_log`（版本与边界）补齐。
- 必须再加：`status`、`sources`、`uncertainty`、`change_log`、`claims`；强烈建议 `pending_human`、`limitations`、`errors`、`degradations`、`runtime`。

字段语义（详见 `references/01_claim_evidence_protocol.md`）：
- `status`：`complete`|`partial`|`failed`|`insufficient_evidence`|`input_error`；缺数据不得写 `complete`。
- `deliverable`：`{draft, claim_list, evidence_table, summary}`，分别指向成稿、论断清单 md、证据表 md 与摘要。
- `claims`：论断清单 `[{claim_id, text, kind, strength, uncertainty}]`。
- `sources`：证据表 `[{claim_id, source_doc, locator, quote, support, reason}]`；`quote` 为逐字原文摘录，`support ∈ {supports, partial_supports, contradicts, insufficient, not_found}`。
- `uncertainty` / `pending_human`：不确定项与待人工确认项。
- `change_log`：`[{time, action, detail}]`，记录 scaffold/extract/build/finalize。
- locator 统一格式：`S{section}.P{para}.s{sentence}`，退化 `P{para}.s{sentence}` / `P{para}` / `page:{n}` / `char[a:b]`；每条判断可指回原文复核。

另写同源可读版 `/app/output/claim_list.md` 与 `/app/output/evidence_table.md`（不新增事实）。

## 4. 执行流程（阶段预算 + 早落盘）

总预算：**900 秒 / 64 次模型请求**，每次回复含 reasoning ≤16384 tokens。按阶段拆预算，**每阶段结束即落盘 partial，绝不因重跑丢失**。

- **P0 读题+早落盘（0–3 min，≤3 请求）**：读 `task.md` + `case.json` + 目录清单；运行 `python scripts/main.py scaffold`。**硬性要求：前 3 分钟内必须先落盘一版 `result.json`（`status=in_progress`），此后只做增量改进。** scaffold 幂等：已存在非 `in_progress` 版本时不覆盖。
- **P1 取料+论断切分（3–12 min，≤20 请求）**：运行 `python scripts/main.py extract` 发现材料、抽取 PDF 文本（`pdftotext -layout`）、启发式切分论断候选到 `/app/work/claims_candidates.json`。Agent 整篇通读原文，按 `references/01` 精化论断清单（合并/拆分/定 kind），不用摘要替代全文。
- **P2 证据匹配+强度分级+成稿（12–13.5 min，≤25 请求）**：为每条论断匹配来源与逐字 quote，判定 `support` 与 `strength`，控制不确定性表述；写 `/app/work/claims.json`、`/app/work/sources.json`、`/app/work/meta.json`（含 summary、draft、uncertainty、pending_human、limitations、status）与 `/app/output/draft.md`。
- **P3 组装+校验（13.5–15 min，≤10 请求）**：运行 `python scripts/main.py build --claims ... --sources ... --meta ...` 增量合入；再 `python scripts/main.py finalize` 校验字段、渲染 claim_list.md / evidence_table.md、写 finished_at。运行 `python scripts/selftest.py` 做交付前自检。
- 预留 ~6 请求 / 60s 处理重试与异常；任一步失败先落盘 partial 并标注。

脚本从 Skill 根目录以相对路径执行；若 cwd 不是 Skill 根，用 `/skills/weekly-skill/scripts/core_impl.py`。

## 5. 自检清单（对应评分维度，逐条可勾选）

- [ ] 论断清单、证据表、成稿三段交付齐备；缺段写明原因。
- [ ] 每条实质论断可指回 `source_doc + locator + 逐字 quote`；quote 与原文一致，无虚构缺陷、无遗漏真实断裂。
- [ ] 每条论断含 `strength` / `uncertainty` / `support` 三要素；强度与材料中的研究设计、样本量相称。
- [ ] 引用真实存在、著录一致；二手转引、预印本、撤稿风险已标注。
- [ ] 相关不写成因果、单篇不写成共识、小样本不写成普适、冲突证据不隐藏。
- [ ] 每条判断可独立复核（locator + quote + source_doc 齐全）。
- [ ] 缺章节/无参考文献/图表公式缺失/格式混乱/截断均写入 `limitations`。
- [ ] 重复运行不覆盖已改进结果（scaffold 跳过非 in_progress）。
- [ ] `python scripts/selftest.py` 全绿（空目录/坏JSON/坏编码/幂等/locator）。

## 6. 降级策略

- 缺稿件文本：`status=partial`/`insufficient_evidence`，只写已验证部分，`limitations` 写明缺口；不得补造正文。
- 缺软件（如无 pdftotext）：用标准库兜底读文本材料；仍失败则 `limitations` 记录，`status=partial`/`failed`。
- 材料冲突/歧义：保留多种解读，写入 `uncertainty`，标注待人工确认。
- 图表/公式缺失或不可读：不猜内容，记 `limitations`；可描述"该处有公式/图但无法读取"。
- 数字/引用/术语无法回溯来源：不写入结论性正文；确需保留则记 `uncertainty`。
- 坏 JSON/坏编码/空目录：捕获并记 `errors`/`degradations`，继续处理其余材料，不崩溃。
- **严禁编造评测分数、运行结果、基准对比或隐藏测试；结果只来自本次实际运行。**

## 7. 最小调用示例（示例，非正式案例）

输入（示例）：`/input/case.json` 含 `case_id` 与 `output_contract`；`/input/material.txt` 含一句待成稿的事实（示例）："研究表明该药显著降低死亡率（风险比 0.72，95% CI 0.61–0.85，P<0.001）。样本量为 1,204 例。"

调用（相对路径）：

```bash
python scripts/main.py scaffold    # 前 3 分钟先落盘 result.json 骨架
python scripts/main.py extract     # 发现材料 + 抽取文本 + 论断候选
python scripts/main.py build --claims /app/work/claims.json \
    --sources /app/work/sources.json --meta /app/work/meta.json
python scripts/main.py finalize    # 校验 + 渲染 claim_list.md / evidence_table.md
```

示意输出（示例，不冒充正式案例结果）：`result.json` 的 `status=complete`；`claims` 含 `C1`（该句，`kind=fact`、`strength=S2`、`uncertainty=none`）；`sources` 含 `C1 → material.txt, locator=P1.s1, quote=原文逐字句, support=supports`。`claim_list.md` 与 `evidence_table.md` 同源可读。

---
name: research-selection-timeboxed
description: 科研选题与创新性评估 Skill。把固定研究问题拆成可检验表述，对已有工作建立可比较证据矩阵，逐条判定创新点覆盖与可行性前提；以 900s/64 请求为硬预算，前 3 分钟先落盘 partial 结果骨架，之后按阶段增量改进，所有结论回链到材料 locator+逐字 quote，输出可复核的 result.json 与 report.md。当需要立项前选题评估、文献证据综述与边界说明时使用。
version: 1.1.0
---

# 科研选题与创新性评估 Skill

## 1. 角色与适用场景

立项/投稿前的选题评估执行者：只依据 `/input` 材料做判断，把结论落到可回查的证据位置，不替作者补造材料。

四件相连的事同时做：**研究问题定义 → 已有工作比较 → 创新点识别 → 可行性判断**，并附检索协议、证据回链、冲突证据与边界说明。判断标准不是篇幅，而是三件事：问题是否拆成可检验表述、创新点是否与最近工作逐条划界、可行性前提是否有出处或被明确标为假设。

不适用：一次性脚本；纯聊天问答；材料为零仍要求给结论（此时返回 `insufficient_evidence` 并列出缺口）。

## 2. 输入契约

运行时固定输入在 `/input`：`case.json`（元信息、可能含 `output_contract`）、`task.md`（任务与评分要点）、材料文件（PDF / 文本 / 表格等）。**实际读文件，不依据文件名或标签猜内容。**

硬规则：

- 文本容错读取（`utf-8-sig` → `utf-8` → `latin-1` 降级）；坏 JSON/坏编码登记到 `errors`/`degradations`，不崩溃。
- 单文件 >2MB 跳过并登记；不因为标签猜测内容。
- `case.json` 缺失或输入目录缺失 → `input_error`；有输入但无可读材料 → `insufficient_evidence`。

## 3. 输出契约

必须产出 `/app/output/result.json`，并同步产出 `/app/output/report.md`。顶层字段：

- `case_id`：保留输入标识；缺省按输入目录哈希回退并标 `unresolved-`。
- `status`：`ok` / `partial` / `insufficient_evidence` / `input_error`，诚实取值。
- `output_contract`：原样回显 `case.json` 的 `output_contract`；其每个键同时回填到顶层（缺值给 `null`/`[]`），不改名不删未知键。
- `research_question` / `search_protocol` / `prior_work` / `novelty` / `feasibility`：四项交付 + 检索协议。
- `evidence_matrix`：每条判断的证据（`source_id`、`locator`、逐字 `quote`、`field`、`status`）。
- `conflicts`：相反证据（`topic`、两方记录、取舍理由）。
- `synthesis`：`conclusions` / `research_gaps` / `boundary_statement`。
- `sources` / `uncertainty` / `change_log`：来源清单；偏倚/空白/无法确定；按时间顺序的改动记录。
- `pending_human` / `errors` / `degradations` / `limits` / `runtime`。

**证据定位**：`locator` 形如 `task.md#L3`、`materials/paper1.txt#L12`、`materials/paper.pdf#p2`（PDF 页码，对应 `work/extracted/*.pages.txt` 的 `===== PAGE n =====` 段）。`quote` 必须逐字照抄；无引用信息写 `pending_human` 或 `not_reported`，不编造 DOI/数值。

## 4. 执行流程与阶段预算

总预算 **900 秒 / 64 次模型请求 / 单响应 ≤16384 tokens**。阶段预算：

| 阶段 | 时间窗 | 时间/请求 | 动作 |
|---|---|---|---|
| phase0 | 0–180s | 180s / 3 | `python3 -B scripts/main.py skeleton`，落盘 partial 骨架 + input_manifest |
| phase1 | 180–360s | 180s / 8 | `scripts/main.py evidence`，发现材料、抽 PDF、刷新 sources |
| phase2 | 360–690s | 330s / 40 | 依次写 4 个 fragment（每项 ≤10 请求） |
| phase3 | 690–840s | 150s / 10 | 写 05_synthesis：evidence_matrix / conflicts / uncertainty / synthesis |
| phase4 | 840–900s | 60s / 3 | `scripts/main.py finalize` 合并、校验、写 report.md |

**硬性早落盘（本 Skill 的核心纪律）**：phase0 在 180 秒内、在任何文献分析之前先执行 `skeleton`，磁盘上立即出现一版合法 partial `result.json`；此后每一步只做增量改进。phase2/phase3 每写完一个 fragment 就落盘一次；`finalize` 是幂等的 last-chance merge，随时可重跑，剩余时间不够也能把已有 fragment 合并成合法结果。每次发出模型推理前执行 `scripts/main.py bump` 记一次自报台账，便于监控是否超预算。

**fragment 规范**：写到 `/app/work/fragments/01_question.json`、`02_prior.json`、`03_novelty.json`、`04_feasibility.json`、`05_synthesis.json`，键名与 `result.json` 一致，只含该项内容。四项交付必须相互引用同一证据集。

1. `research_question`：拆成 研究对象 / 关键变量 / 比较基线 / 预期结论 / 可证伪条件，形成可检验陈述；材料没给齐就标 `status=partial/missing` 并列缺口。
2. `prior_work`：按 任务/方法/数据/评价方式/结论 建比较矩阵，标出不可直接比较的原因；禁止跨文档串值。
3. `novelty`：区分 方法创新/数据创新/场景迁移/结论修正，逐条判 `verdict=novel|covered|partially_covered|unverifiable`，给 `covered_by` 与理由+证据。
4. `feasibility`：核查 数据/算力/实验条件/时间窗口/人员能力，每条标 `reported|assumed|missing|conflicting`，`assumed` 必须显式标注。
5. `synthesis`：`conclusions`（结论到证据为止）+ `research_gaps` + `boundary_statement`；冲突证据进 `conflicts`。

## 5. 自检清单（逐条可勾选，对应评分维度）

- [ ] `research_question.testable_statement` 含对象/变量/基线/可证伪条件（任务完成度）。
- [ ] 四项交付齐全且引用同一证据集（正确性）。
- [ ] 覆盖问题拆解、检索覆盖、证据回链、冲突证据、综合结论、研究空白六类内容（完整性）。
- [ ] 每条结论在 `evidence_matrix` 有 locator+逐字 quote；检索式/纳排规则写入 `search_protocol`（可核验性）。
- [ ] 每条 novelty 有 `covered_by` 与 `verdict`，场景迁移/结论修正未误判为方法创新（创新增益）。
- [ ] 每条 feasibility 有 `reported|assumed|missing|conflicting` 标注（可行性）。
- [ ] 冲突证据进 `conflicts` 并说明取舍；"未报告"≠"不存在"（可靠体验）。
- [ ] `status` 与 `errors/degradations/pending_human/limits` 一致，无编造（可靠体验）。
- [ ] phase0 已在 3 分钟内落盘 result.json（运行稳定性）。
- [ ] 同一输入两次运行结论一致，`change_log` 记录改动（生态贡献）。
- [ ] 提交前跑 `python3 -B scripts/main.py selftest`，三项场景全部 PASS。

## 6. 降级与诚实性

- 缺数据：对应字段保持 `null`/`[]`，写入 `uncertainty.gaps`，status 降级；**不编造数据、DOI、分数或运行结果**。
- 缺软件（`pdftotext` 不可用）：登记到 `degradations`，PDF 标 `text_unavailable`，继续处理其余文本材料。
- 冲突/偏倚/空白：登记到 `conflicts` 与 `uncertainty`；推断标注 `inferred`，不把推断写成事实。
- 写盘失败：脚本降级写最小合法结果，任何命令退出码 0（不崩溃）；校验问题只写入 `errors`，不中断。

## 7. 脚本与资源

- `scripts/main.py`：统一入口（`skeleton`/`evidence`/`finalize`/`bump`/`selftest`）。
- `scripts/core_impl.py`：I/O、契约回显、PDF 抽取、合并与校验的实现（仅标准库）。
- `scripts/selftest.py`：提交前自检。
- `references/assessment-rules.md`：四项交付的判定口径与证据字段。
- `references/checklist.md`：评分维度对照清单。

脚本只用 Python 标准库；`pdftotext` 为平台预装工具，缺失时降级。调用一律用相对路径 `scripts/main.py`。

## 8. 最小调用示例（示例，非正式案例）

```bash
cd /skills/weekly-skill
python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work
python3 -B scripts/main.py evidence --input /input --work /app/work --output /app/output
# ……在 /app/work/fragments/ 写入 01_question.json 等 fragment，每写完一个即落盘……
python3 -B scripts/main.py finalize --input /input --output /app/output --work /app/work
python3 -B scripts/main.py selftest
```

示意 fragment（`03_novelty.json`，标注为示例）：

```json
{
  "novelty": {
    "claims": [{
      "claim": "示例：把方法 X 迁移到新场景",
      "kind": "scenario_transfer",
      "verdict": "partially_covered",
      "covered_by": "材料中已有工作 W",
      "reason": "场景不同，但核心方法无本质改动",
      "evidence": [{"source_id": "materials_w_txt", "locator": "materials/w.txt#L10", "quote": "原文逐字片段"}]
    }]
  }
}
```

正式运行时以 `/input` 真实材料为准，以上 JSON 仅示意结构，不冒充案例答案。

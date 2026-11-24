# 交付自检清单（checklist.md）

按评分维度组织，交付前逐条核对；勾选状态与 `result.json` 的
`status / errors / degradations / pending_human / limitations` 保持一致。

## 实际效果（40 分）

- [ ] `research_question.testable_statement` 含 对象/变量/基线/预期/可证伪条件 五要素。
- [ ] `prior_work` 比较矩阵字段齐全，真正相近的工作选对，不可比原因具体。
- [ ] 每条 `novelty.claims[*]` 有 `category + verdict + covered_by + rationale`，
      场景迁移/结论修正未误判为方法创新。
- [ ] 每条 `feasibility.preconditions[*]` 有 `state`（reported/assumed/missing/conflicting），
      assumed 显式标注且进 `uncertainty.gaps`。
- [ ] 六类内容全覆盖：问题拆解、检索覆盖、证据回链、冲突证据、综合结论、研究空白。
- [ ] `search_protocol` 写清检索式与纳排规则，可独立复核。
- [ ] 每条关键结论在 `evidence_matrix` 有 `locator + quote`（逐字）。

## 使用增益（25 分）

- [ ] 结论推进到可决策：能回答“增量在哪 / 能否做成”，不是四平八稳综述。
- [ ] 读 report.md + result.json 即可定位每条结论的来源与缺口，无需返工。
- [ ] `change_log`、`evidence_matrix`、`report.md` 三处一致。

## 创新增益（15 分）

- [ ] 形成基础 Agent 难以稳定实现的能力：四项交付 + 证据回链 + 状态诚实推导。
- [ ] 问题拆解、比较维度、新颖性判定、可行性核查口径已写在 `references/assessment-rules.md`。
- [ ] 适用/不适用边界写在 SKILL.md 第 1 节，新增判断的价值有说明。

## 可靠体验（10 分）

- [ ] 触发路径清楚：读 SKILL.md → `skeleton` → `evidence` → 写 fragment → `finalize`。
- [ ] 空输入 / 坏 JSON / 缺参数 / 缺软件 / 写盘失败均有降级，不崩溃、不编造。
- [ ] 同输入两次运行 `result.json` 字节一致；`scripts/selftest.py` 全部 PASS。

## 生态贡献（10 分）

- [ ] 输入契约、评估口径、依赖、适用范围、限制、维护信息齐全。
- [ ] 比较矩阵、证据表、评分锚点、脚本可被他人复用（仅标准库）。
- [ ] 说明与提交版本一致，无示例答案冒充正式案例结果。

## 确定性专项（本变体侧重）

- [ ] `result.json` 经 `canonical_dumps(allow_nan=False)` 写出，无 NaN/Infinity。
- [ ] 文件发现按相对路径排序；case_id 回退哈希不含随机数、不含挂钟时间。
- [ ] `result.json` 不写挂钟时间戳；耗时只进 `work/run.log`。
- [ ] `finalize` 幂等：连续两次合并字节一致；`change_log` 幂等重建而非追加。

## 快速命令

```bash
python3 -B scripts/main.py --help                 # 退出码 0
python3 -B scripts/selftest.py --help             # 退出码 0
# 正式运行（在 /skills/weekly-skill 下）：
python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work
python3 -B scripts/main.py evidence --input /input --output /app/output --work /app/work
# …… Agent 按顺序写 /app/work/fragments/{00_meta,01_question,02_prior,03_novelty,04_feasibility,05_synthesis}.json ……
python3 -B scripts/main.py finalize --input /input --output /app/output --work /app/work
# 自检（本地需传入实际路径）：
python3 -B scripts/main.py selftest --skill . --input /input --output /app/output --work /app/work
```

## 最小调用示例（示意，非正式案例结果）

输入目录假设（示意）：

```text
/input/case.json      # {"case_id":"C1","output_contract":{...}}
/input/task.md        # 任务说明
/input/materials/p1.txt
/input/materials/p2.pdf
```

调用顺序（在 `/skills/weekly-skill` 下，均为相对路径）：

```bash
python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work
python3 -B scripts/main.py evidence --input /input --output /app/output --work /app/work
# Agent 写 /app/work/fragments/01_question.json 等四项交付 + 05_synthesis.json
python3 -B scripts/main.py finalize --input /input --output /app/output --work /app/work
```

`result.json` 顶层结构示意（示意，非正式案例结果）：

```json
{
  "case_id": "C1",
  "status": "ok",
  "output_contract": {"research_question": null, "prior_work": null, "novelty": null, "feasibility": null, "evidence_matrix": []},
  "research_question": {"testable_statement": "…", "object": "…", "variables": ["…"], "baseline": "…", "expected": "…", "falsification": "…", "status": "complete"},
  "prior_work": {"comparison_matrix": [], "nearest_works": [], "notes": "…"},
  "novelty": {"claims": [{"claim": "…", "category": "method", "verdict": "novel", "covered_by": "…", "rationale": "…", "evidence_refs": ["ev-1"]}]},
  "feasibility": {"preconditions": [{"item": "…", "category": "data", "state": "reported", "basis": "…", "evidence_refs": ["ev-1"]}], "conditional_conclusion": "…", "failure_risks": []},
  "evidence_matrix": [{"evidence_id": "ev-1", "source_id": "src-001", "locator": "materials/p1.txt#L3", "quote": "逐字原文", "field": "…", "supports": "supports", "status": "ok"}],
  "conflicts": [],
  "synthesis": {"conclusions": [], "research_gaps": [], "boundary_statement": "…"},
  "sources": [{"source_id": "src-001", "path": "materials/p1.txt", "type": "material", "bytes": 0, "extracted_chars": 0, "status": "ok"}],
  "uncertainty": {"biases": [], "gaps": [], "unverifiable": [], "confidence": "medium"},
  "pending_human": [],
  "errors": [],
  "degradations": [],
  "limitations": [],
  "runtime": {"budget_seconds": 900, "budget_requests": 64, "max_tokens_per_response": 16384, "phases": []},
  "change_log": []
}
```

`output_contract` 中的每个键都必须同时出现在顶层；本示例为示意结构，不代表任何正式案例的答案。

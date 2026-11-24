# 运行协议：六阶段多步工具调用（供 Harness Agent 依序执行）

本协议把 900 秒 / 64 次模型请求的总预算切成六阶段，每阶段写清输入、输出、工具调用与停止条件。所有实质内容写进 `/app/work/`，由脚本确定性组装；工具调用全程记日志，保证路径可复跑。

## 通用纪律

- 每次工具调用（search/fetch/PDF 解析/表格抽取/计算）都追加一行 `/app/work/tool_call_log.jsonl`，字段：
  `{"ts", "step", "tool", "params", "ok", "summary", "error"}`。事后"某结论从哪来"只从该日志与 evidence_cards 回答。
- 证据追加用 `/app/work/evidence_cards.jsonl`，每行一条：`{"id", "subquestion", "source", "locator", "quote", "claim", "judgment", "reason", "conclusion_ids"}`。
- 每次落盘都是增量：先 `skeleton`，再反复 `merge --payload`，最后 `finalize`。**禁止**清空重建 `/app/output/result.json`。

## P0 骨架（0–3 min，约 4 次请求）

- 输入：`/input/case.json`、`/input/task.md`、材料清单。
- 动作：`python3 -B scripts/main.py discover` 看清单；读 case.json 与 task.md；然后 `python3 -B scripts/main.py skeleton`。
- 停止条件：`result.json` 已存在且可 `json.load`，`status` 为 `partial`/`insufficient_evidence`/`input_error` 之一。
- 输出：合法 partial 骨架 + `run_state.json`。此后任何失败都不丢结果。

## P1 拆解（3–12 min，约 8 次请求）

- 输入：题面研究问题 + 材料目录。
- 动作：把研究问题拆成 2–8 个可独立检索子问题 `SQ1..SQn`，每条写对象/机制/方法/时间范围/比较关系/证据要求；标注依赖边（前置/可并行）。
- 输出：`/app/work/decomposition.md` 与 `/app/work/search_plan.md`。
- 停止条件：每个 SQ 都有检索动作、每个依赖边已标注；无两个 SQ 指向完全相同检索式。

## P2 检索取证（12–35 min，约 32 次请求）

- 输入：`search_plan.md` 指定的检索顺序与来源范围。
- 动作：按依赖顺序调用 search → fetch → PDF 解析/表格抽取；每次调用记 tool_call_log；从材料抽取逐字片段，追加 evidence_cards.jsonl。
- 停止条件：每个 SQ 至少 1 条证据、或检索为空/工具报错/限流已记录且无新来源；同一检索式不重复执行。
- 失败处理：PDF 无文本层记 `judgment=extraction_failed`，quote 留空并记 `reason`；不硬读像素。

## P3 综合（35–50 min，约 10 次请求）

- 输入：evidence_cards.jsonl 全部记录。
- 动作：证据归并到子问题；冲突证据并列；写结论句（每条带 evidence_ids）；整理 gaps/pending_human；组装 payload.json。
- 动作：`python3 -B scripts/main.py merge --payload /app/work/payload.json`。
- 停止条件：每条结论 evidence_ids 均命中证据矩阵；冲突不静默择一。

## P4 收尾（50–60 min，约 6 次请求）

- 动作：`python3 -B scripts/main.py finalize`；再 `python3 -B scripts/main.py selfcheck`。
- 对自检 FAIL 项逐个修复：补 quote、补来源、把强度词降级、把 gap 写具体。
- 停止条件：`selfcheck` 输出 `结论: PASS`（V11 逐字回源可通过或已如实标 unverifiable）。

## 停止 / 降级总则

- 缺材料：`status=insufficient_evidence`，`pending_human` 列出缺口。
- 部分覆盖：`status=partial`，`limitations` 说明范围。
- 冲突无法裁定：`undetermined`，两个来源 locator+quote 都保留。
- 任何阶段超时：立即 `finalize` 落盘当前 partial，不空手。

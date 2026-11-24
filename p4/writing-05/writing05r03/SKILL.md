---
name: evidence-driven-writing
description: 文献证据驱动写作 Skill。把科研写作任务拆成"论断清单—证据表—成稿"三段交付：从全文而非摘要抽取可回查的论断，为每条论断匹配逐字原文摘录并标注证据强度（L1–L4/U）、支撑程度与不确定性，产出可独立复核的成稿与证据表。用于需要逐句可溯源、事实与来源一致的学术写作、改写、校对、引用核验与结构化写作任务。
version: 1.0.0
---

# 文献证据驱动写作（evidence-driven-writing）

## 1. 角色与何时使用

本 Skill 面向"证据可回查"的学术写作：目标不是让模型多写，而是让每一句有依据的话都能被指认**依据在哪、依据有多强**。核心动作是先把稿件拆成可判定论断，再逐条匹配来源、标注证据强度与不确定性，最后据此成稿。

适用：需要从给定科研材料（论文、底稿、数据）产出写作产物，并保留来源与修改记录的任务——生成、改写、校对、引用核验、结构化写作。
不适用（如实说明并退出）：纯语言润色且明确不要求证据溯源；无任何材料正文；要求输出整篇重写而不需要证据表。

## 2. 输入契约

运行时原始输入固定在 `/input/`：
- `/input/case.json`：任务说明、材料路径或内嵌内容、输入约束、`output_contract`、评分证据范围；
- `/input/task.md`：题目要求；
- 其他材料（论文 PDF/txt/md、图表、数据）按 case.json 提供；PDF 只有原文，无预解析答案层。

工作目录 `/app/work/`；最终产物放 `/app/output/`。脚本调用用相对路径 `scripts/core_impl.py`。
铁律：以实际读到的文件内容为准，不按标签或文件名补造内容；缺失、冲突、异常分别记录，不新增材料外事实。

## 3. 输出契约

必须产出 `/app/output/result.json`，同时渲染可编辑的 `/app/output/evidence_table.md` 与 `/app/output/manuscript.md`（二者与 result.json 同源，不新增事实）。

- `case_id`：取自 case.json（`case_id` > `caseId` > `id`）。
- 题目 `output_contract` 的全部字段：回显 case.json 中 `output_contract`/`outputContract`/`contract`/`result_schema` 的每个键；缺失时按默认契约 `deliverable`、`sources`、`change_log` 补齐。未知键保留原值，不得改名或删除。
- 必须再加：`status`、`deliverable`、`sources`、`uncertainty`、`pending_human`、`change_log`、`limitations`、`runtime`。

字段语义：
- `status`：`complete` | `partial` | `failed`；缺数据不得写 `complete`。
- `deliverable`：主结果 = 成稿 + 论断清单 + 证据表 + 摘要。每条论断含 `claim_id`、`claim_text`、`claim_type`、`support`（逐条证据）、`verdict`；每条证据含 `source_doc`、`locator`、逐字 `quote`、`support_level`、`strength`、`evidence_type`、`strength_reason`。
- `sources`：逐条 `{key, value, kind, location, source_doc, change}`；数字、术语、引用、图表、关键论断均可回溯。
- `uncertainty`：判断不确定、证据不足、需人工确认处。
- `pending_human`：需要人工确认的事项（如预印本是否已见刊、转引核实）。
- `change_log`：`[{time, action, detail}]`，记录 scaffold/analyze/finalize。
- `limitations`：材料缺口、缺软件、截断、异常、未确认项。

locator 统一格式：`{source_doc}#{unit}`，`unit` 为 `Sec{节}.P{段}`、`P{段}`、`p{页}`、`L{行}` 或字符区间。判定口径见 `references/01_evidence_method.md`。

## 4. 执行流程（900s / 64 请求 / 16384 tokens）

- **P0 读题 + 落骨架（0–3 min，≤3 请求）**：读 task.md + case.json + 目录清单；运行 `python scripts/main.py scaffold --case /input/case.json --task /input/task.md --out /app/output --work /app/work`。**硬性要求：前 3 分钟内必须先落盘一版 partial 结果骨架，此后只做增量改进，绝不因重跑而丢失。**
- **P1 取料 + 提取（3–12 min，≤20 请求）**：通读全部材料全文，不用摘要替代；PDF 用 `pdftotext -layout <pdf> /app/work/<name>.txt`。输出 `/app/work/analysis.json` 骨架与材料清单。
- **P2 论断—证据匹配（12–13.5 min，≤25 请求）**：按 `references/01_evidence_method.md` 切分论断、匹配证据、标注强度与不确定性，写入 `/app/work/analysis.json`（字段见该规范与 scripts/core_impl.py）。缺项写"材料不足"，不为凑数虚构。
- **P3 组装 + 校验（13.5–15 min，≤10 请求）**：运行 `python scripts/main.py finalize --analysis /app/work/analysis.json --case /input/case.json --out /app/output --work /app/work`，再 `python scripts/main.py validate --result /app/output/result.json`。
- 预留 ~6 请求 / 60s 处理重试与异常；任一步失败先落盘 partial 并标注。

脚本从 Skill 根目录以相对路径执行；若 cwd 不是 Skill 根，用绝对路径 `/skills/weekly-skill/scripts/core_impl.py`。

## 5. 自检清单

见 `references/02_selfcheck.md`（逐条可勾选，对应五大评分维度）。运行后必须执行：

```bash
python scripts/main.py validate --result /app/output/result.json   # 退出码 0
python scripts/selftest.py                                          # 退出码 0
```

## 6. 降级策略

- 缺材料正文：`status=partial`，只写已验证部分，`limitations` 写明缺口；不得补造正文。
- 缺软件（如无 pdftotext）：用标准库兜底；仍失败则 `limitations` 记录，`status=partial/failed`。
- 材料冲突/歧义：保留多种解读，写入 `uncertainty`，标 `pending_human`。
- 图表/公式缺失或不可读：不猜内容，记 `limitations`，可描述"该处有公式/图但无法读取"。
- 数字/引用/术语无法回溯来源：不写入 deliverable；确需保留则记 `uncertainty` 或 `pending_human`。
- 空输入/坏 JSON/坏编码：退出码 0，写合法 partial/failed，不抛异常。
- 严禁编造评测分数、运行结果、基准对比或隐藏测试；结果只来自本次实际运行。

## 7. 确定性保证

同一输入（相同 case.json + 相同 analysis.json）两次运行产物一致：

- 论断按 `claim_id` 排序、证据按 `(claim_id, source_doc, locator)` 排序、sources 按 `key` 排序；JSON 用 `sort_keys=True` 稳定序列化。
- `finalize`/`scaffold` 支持 `--fixed-time`：给定后 `change_log`/`runtime` 时间戳确定，产物字节级一致；`scripts/selftest.py` 据此验证字节级可复现。
- `scaffold` 不覆盖已 `complete`/`failed` 的 result.json，只增量改进。

## 8. 最小调用示例（示例，非正式案例）

```bash
python scripts/main.py scaffold --case ./demo/input/case.json --task ./demo/input/task.md --out ./demo/output --work ./demo/work
# Agent 通读材料，写 ./demo/work/analysis.json（字段见 references/01_evidence_method.md）
python scripts/main.py finalize --analysis ./demo/work/analysis.json --case ./demo/input/case.json --out ./demo/output --work ./demo/work
python scripts/selftest.py
```

示意输出仅用于说明接口与流程，不冒充正式案例结果。

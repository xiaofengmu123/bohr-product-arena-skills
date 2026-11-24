---
name: citation-tracing
description: 引文追踪与关联文献发现。以种子论文为起点，沿后向参考文献与前向施引文献做多层级扩展，用共被引、文献耦合、作者机构与数据集任务关联发现平行工作，完成去重与身份合并；本变体以结构化中间表示（plan/paper_registry/edges/evidence_ledger）为核心，保证每条结论、每条边、每次数值都能逐字回溯到材料。当任务要求“顺藤摸瓜”式文献检索、引用网络构建、路径解释或论文身份对齐时使用。
version: 1.1.0
---

# 引文追踪与关联文献发现

## 1. 角色与何时使用

本 Skill 把“从种子论文追踪文献”沉淀为确定性流程：前向/后向引用扩展、平行工作发现、身份合并、引用网络与路径解释。适用：以种子论文为起点做多层级文献扩展、引用关系溯源、去重与身份对齐、关键结论复核。不适用：普通关键词检索、全文阅读理解、引文语境内容分析、未经授权的数据抓取。判断是否做对只看三件事：扩展文献能否顺着引用边说清来路；每条边是否有可回指的数据源依据；去重合并后的清单是否干净到可直接进综述。

## 2. 输入契约

运行时输入固定为：`/input/case.json`（案例元数据，可能含 `case_id`、`output_contract`、种子论文信息与材料清单）、`/input/task.md`（任务说明）、`/input/` 下可能另有 PDF、JSON、CSV、TXT 材料。不假设固定文件名或键名：先扫描目录，再按内容识别。文本按 `utf-8-sig` 优先容错读取，坏编码降级；单文件超过 2MB 跳过并记 `degradations`。实际输入文件与原论文是唯一事实依据，不依据题面领域标签补造数据。

## 3. 中间表示（本变体核心：意图→证据→实体→关系→结果）

在 `/app/work/` 维护五份结构化中间产物，最终 `result.json` 只能由它们确定性投影而来，任何一条结论都能经中间表示回到材料原文：

- `plan.json`——意图层：`research_question`、`seed`、`seed_key`、`strategy`（层数/方向/剪枝/停止判据）、`coverage_targets`、`paths_to_explain`、`output_contract` 原文。
- `paper_registry.json`——实体层：`clusters[]`，每簇含 `canonical_id`、`members[]`（预印本/会议版/期刊版/增补版）、`merge_key_used`、`merge_evidence`、`confidence`。
- `edges.json`——关系层：`edges[]`，每条统一 `source` cites `target` 的有向边，含 `direction`、`source_label`、`target_label`、`evidence`。
- `evidence_ledger.json`——证据层：`entries[]`，每条含 `claim`、`locator`、逐字 `quote`、`source`、`status`（confirmed/conflict/not_reported）。
- `state.json`——运行层：`phase`、时间戳、预算、`metrics`（用于证明使用增益）、`degradations`、`errors`、`change_log`。

分工原则：Agent 只编辑中间表示（用改写工具或 `python3 scripts/main.py dedupe/rank/path`），不改 finalize 产物；`finalize` 只做从中间表示到最终产物的无歧义投影。

## 4. 输出契约

工作目录 `/app/work`，最终产物写 `/app/output/`。必须产出 `/app/output/result.json`（评分主产物）；辅助可复核产物由 `finalize` 生成：`report.md`、`papers.csv`、`edges.csv`、`citation_graph.svg`。

`result.json` 必须包含：`case_id`；题目 `output_contract` 的全部字段（运行时从 `/input/case.json` 读取并原样回填键，缺值填 `null`/`[]`，不改名、不删未知键，但不覆盖运行状态字段）；`status`（`ok | partial | insufficient_evidence | input_error`）；`sources`（每条带 `locator` 与逐字 `quote`）；`uncertainty`（冲突/缺失/覆盖缺口）；`change_log`（每次修改的时间+内容+原因）；`results.papers`（稳定 `id`、元数据、版本列表、来源、合并证据）；`results.edges`（有向引用边）；`results.clusters`（身份合并簇）；`results.paths`（逐跳路径）；另含 `seed`、`seed_key`、`research_question`、`limitations`、`pending_human`、`errors`、`degradations`、`runtime`、`metrics`。

输出原则：先写合法 partial 骨架再分析；任何异常都不留空文件；状态诚实，缺材料就 `insufficient_evidence`，不编造文献、数值、引用、分数或软件状态。

## 5. 执行流程与预算

总预算 **900 秒、64 次模型请求**、单次回复含推理 ≤16384 token。**前 3 分钟内必须先落盘 `result.json` 骨架与五份中间表示**，此后只对同一文件增量修改，每次修改都 append `change_log`。

- Step 0（0–3 分钟，≤4 次请求）建骨架：`python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work`。此步必须写盘成功；即使失败也退出码 0 并留最小合法 JSON。
- Step 1（3–9 分钟，约 30 次请求）核实种子+一层扩展：读材料，确认种子身份（DOI/arXiv/标题），写入 `plan.seed`/`plan.seed_key`；后向抽参考文献（种子 cites 出处）、前向抽施引文献（谁 cites 种子）。每条边写 `source/target/relation=cites/source_label/target_label/evidence`，证据逐字留档到 `evidence_ledger`。
- Step 2（9–13 分钟，约 20 次请求）多层扩展+去重+平行发现：按需做 2–3 层扩展并剪枝；`python3 scripts/main.py dedupe --work /app/work` 做身份合并；用共被引/文献耦合/作者机构/数据集任务关联找不直接引用的平行工作并写明依据；识别奠基/桥接/热点节点，剪枝原因写入 `plan.pruned`。
- Step 3（13–15 分钟，≤8 次请求）排序+路径+自检：为关键远端文献在 `plan.paths_to_explain` 写明起止并复核路径方向；核对下面的自检清单；最后 `python3 scripts/main.py finalize --output /app/output --work /app/work` 生成报告/CSV/SVG 并校验。预留最后 60 秒给 finalize；时间不足时先保证 result.json 合法，再补 artifacts。

任何中途变更都用 `change_log` 记录时间+内容+原因；`finalize` 由 `state.change_log` 透传，不丢历史。

## 6. 核心方法约定

- 边方向统一：`source` cites `target`。后向=种子为 `source`；前向=种子为 `target`；外部数据只给被引列表不给方向时标 `direction_unknown`，不得猜测。
- 身份键优先级固定（见 `citation_lib.key_of`）：DOI → arXiv → title+第一作者姓+年份 → normalized title；保留合并证据（成员、键类型、键值、置信度）。
- 排序（`citation_lib.rank_papers`）：距离层数主导，被引次数仅作弱信号（**高被引不等于高相关**），年份只对近期作品轻微加权，来源置信度作乘数，时间主题相似不等于方法相关。
- 停止判据：层数上限默认 3；按相关性/年份/文献类型剪枝；无新节点即停；剩余时间 <120 秒或剩余请求 <8 即收束。
- 证据硬要求：每个关键结论/边/数值给 `locator`（如 `materials/x.pdf#p3`）+ 逐字 `quote`；冲突信息两者都记并标 `source_conflict`；缺失标 `not_reported`，不补造。

## 7. 自检清单（对评分维度，提交/交付前逐条勾选）

- [ ] 实际效果：种子身份已在材料中核实并写 `plan.seed_key`；
- [ ] 实际效果：每条边方向正确、有来源证据，无猜测方向；
- [ ] 实际效果：同文多版本已合并，合并证据可复核；
- [ ] 实际效果：关键被引出处、主要后续改进、代表性平行工作均已覆盖或注明遗漏；
- [ ] 使用增益：去重与方向规范化由脚本完成，减少人工核验条目数与重复翻页；
- [ ] 使用增益：同输入两次运行产物一致（脚本确定性，`results` 逐字节稳定）；
- [ ] 创新增益：中间表示五层分离、跨源身份对齐、方向感知扩展、路径解释至少落地一项并可复核；
- [ ] 可靠体验：空输入/坏 JSON/坏编码不崩溃、退出码 0；
- [ ] 可靠体验：种子无引文记录、同名论文歧义、跨库字段冲突、撤稿更正、限流/权限不足均已识别并写 `errors`/`degradations`；
- [ ] 生态贡献：文档齐备、边表字段与去重规则、中间表示 schema 可复用。

自检命令：

```
python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work
python3 scripts/main.py finalize --output /app/output --work /app/work
python3 scripts/main.py selftest --root .
python3 -c "import json;r=json.load(open('/app/output/result.json',encoding='utf-8'));assert r.get('case_id') and r.get('status');print(r['case_id'],r['status'])"
```

## 8. 降级策略

- 缺数据/缺引文记录：状态降为 `insufficient_evidence` 或 `partial`，写 `pending_human` 与 `limitations`，不补造。
- 缺软件/渲染工具：脚本只用 Python 标准库；SVG 始终可产出，PDF/PNG 预览缺省时在 `limitations` 标 `preview_missing`。
- 坏 JSON/坏编码：捕获并记 `errors`/`degradations`，继续处理其余材料，退出码 0。
- 接口限流/权限不足：记录到 `errors`/`degradations`，已获部分照常交付并标注边界。
- 任何中途异常：保留已落盘的 partial `result.json` 与中间表示，append `errors`/`change_log`，进程不崩溃。

## 9. 脚本与命令（相对路径，仅标准库）

- `scripts/main.py`：入口（bootstrap/finalize/validate/dedupe/rank/path/selftest）。
- `scripts/core_impl.py`：实现；`scripts/citation_lib.py`：纯函数；`scripts/selftest.py`：自检封装。
- 常用：`python3 scripts/main.py validate --work /app/work --output /app/output`；`python3 scripts/main.py path --work /app/work --from P1 --to P9`。
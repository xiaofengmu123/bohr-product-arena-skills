---
name: citation-tracing
description: 引文追踪与关联文献发现。从种子论文出发，沿后向参考文献与前向施引文献做多层级扩展，用共被引、文献耦合、作者或数据集关联发现平行工作，完成去重与身份合并，输出可复核的文献集、有向引用边、证据路径与诚实状态。当任务要求"顺藤摸瓜"式引文检索、引用网络构建、路径解释或论文身份对齐时使用。
version: 1.1.0
---

# 引文追踪与关联文献发现

## 1. 角色与适用场景

把"从种子论文追踪文献"沉淀为可复用、确定性、契约对齐的流程。适用：前向/后向引文追踪、引用网络构建、路径解释、去重与身份合并、关联文献发现。不适用：纯关键词检索、全文阅读、引文语境内容分析、未经授权的数据抓取。判断做对只看三件事：扩展文献能否顺着引用边说清来路；每条边是否有可回指的数据源依据；去重合并后清单是否干净到可直接进综述。

本版首要设计取舍是**契约驱动**：result.json 的字段逐一对齐 `/input/case.json` 的 `output_contract`，未填字段显式占位并追踪 `missing/unfilled`，每一次修改都由脚本自动追加 `change_log`，使"先落盘、再增量改进"可被独立复核。

## 2. 输入契约

运行时输入固定为：

- `/input/case.json`：案例元数据，可能含 `case_id`、`output_contract`、种子论文信息与材料清单；
- `/input/task.md`：任务说明；
- `/input/` 下可能另有 PDF、JSON、CSV、TXT 材料。

不假设固定文件名/键名：先扫描目录再按内容识别。文本按 `utf-8-sig → utf-8 → gb18030 → latin-1` 容错读取；单文件超过 2MB 跳过并记 `degradations`。`case.json` 与 `task.md`、原论文是唯一事实依据；不依据题面领域标签补造数据。

## 3. 输出契约

工作目录 `/app/work`，最终产物写 `/app/output/`。必须产出：

- `/app/output/result.json`（评分主产物）；
- 可复核产物 `report.md`、`papers.csv`、`edges.csv`、`citation_graph.svg`。

`result.json` 必须包含：

- `case_id`；`status`（`ok | partial | insufficient_evidence | input_error`）；
- 题目 `output_contract` 的**全部字段**按原名回填到顶层（缺值 `null`/`[]`/`{}`，不改名、不删键），并在 `contract` 块保存原契约快照 `mirror` 与 `fields/missing/unfilled/satisfied`；
- `sources`（每条带 `locator` + 逐字 `quote`）、`uncertainty`（冲突/缺失/覆盖缺口）、`change_log`（每次修改的 `time + action + detail + reason`）；
- `results.papers`（稳定 `id` + 元数据 + 证据）、`results.edges`（统一 `source` cites `target`）、`results.clusters`、`results.paths`、`results.pruned`、`results.graph_summary`；
- `seed`、`seed_key`、`research_question`、`limitations`、`pending_human`、`errors`、`degradations`、`runtime`。

原则：写合法 partial 骨架再分析；任何异常不留空文件；状态诚实，缺材料即 `insufficient_evidence`，不编造文献、数值、引用或分数。字段语义详见 `references/contract-schema.md`。

## 4. 执行流程与时间预算

总预算 **900 秒、64 次模型请求、单响应 ≤16384 tokens**。**前 3 分钟必须先落盘 result.json 骨架**，此后只对同一文件增量修改（每次 append `change_log`）。

Step 0（0–3 min，≤4 次请求）——骨架：

```
python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work
```

扫描输入、提取 `output_contract`、写出契约对齐骨架 + `/app/work/state.json`。必须退出码 0；缺 `case.json` 时状态记 `insufficient_evidence`，坏 JSON 记 `input_error`，均不留空文件。

Step 1（3–9 min，约 30 次请求）——核实种子 + 一层扩展：读取材料确认种子身份（DOI/arXiv/标题），用 `update` 写 `seed_key`；后向抽参考文献、前向抽施引文献。每条边写 `source/target/relation=cites/evidence`，**逐字证据落盘**。

Step 2（9–13 min，约 20 次请求）——多层扩展 + 去重 + 平行发现：按需 2–3 层并剪枝；`dedupe`/`merge_papers` 做身份合并；用共被引/文献耦合/作者机构/数据集关联找平行工作并注明依据；识别奠基/桥接/热点节点。

Step 3（13–15 min，≤8 次请求）——排序 + 路径 + 自检：更新排序与 `paths`（逐跳）；核对评分维度；运行：

```
python3 scripts/main.py finalize --output /app/output --work /app/work
python3 scripts/main.py update --output /app/output --file <instruction>.json   # 增量填字段
python3 scripts/main.py selftest
```

预留最后 60 秒给 finalize 与校验；时间不足先保证 result.json 合法，再补 artifacts。

`update` 指令为 JSON 对象或数组，支持 `set / append / upsert_paper / add_edge / merge_papers / dedupe / set_status / set_contract / rank / paths / summary`；示例：

```json
{"op":"set_contract","field":"candidate_papers","value":["p1"],"reason":"从材料 x.pdf#p2 确认"}
{"op":"upsert_paper","paper":{"id":"p1","title":"...","doi":"...","evidence":[{"locator":"materials/x.pdf#p1","quote":"...逐字..."}]},"reason":"..."}
{"op":"add_edge","edge":{"source":"p2","target":"p1","relation":"cites","source_label":"...","target_label":"...","evidence":[{"locator":"...","quote":"..."}]},"reason":"..."}
```

## 5. 核心方法约定

- 边方向：统一 `source` cites `target`；后向=种子为 source，前向=种子为 target；只给被引列表不给方向时标 `direction_unknown`，不猜测。
- 身份合并优先级：DOI → arXiv id → 标题+第一作者姓+年份 → 标题；保留合并证据到 `merged[]`。
- 排序分数=溯源距离+图内被引度+桥接度+年份小幅加成+证据置信，明确非"高被引=高相关"；逐跳路径用方向感知 BFS。
- 证据：关键结论都给 `locator`+逐字 `quote`；冲突两记并标 `source_conflict`；缺失标 `not_reported`。

## 6. 自检清单（逐条勾选，对应评分维度）

实际效果：

- [ ] 种子身份已在材料中核实并写 `seed_key`；
- [ ] 每条边方向正确、有来源证据，无猜测方向；
- [ ] 同文不同版本（预印本/会议版/期刊版/增补版）已合并且证据可复核；
- [ ] 关键被引出处、主要后续改进、代表性平行工作已覆盖或注明遗漏；

使用增益：

- [ ] 去重与方向规范化由脚本完成，减少人工核验与翻页；
- [ ] 同一输入两次运行内容指纹一致（见 `scripts/selftest.py`）；

创新增益：

- [ ] 契约逐字段对齐、跨源身份合并、方向感知扩展、路径解释至少落地一项；

可靠体验：

- [ ] 空输入/坏 JSON/坏编码不崩溃、退出码 0；
- [ ] 种子无引文记录、同名歧义、跨库冲突、撤稿更正、限流权限不足均已识别并处置；

生态贡献：

- [ ] 文档齐备、边表字段与去重规则可复用，字段语义见 `references/`。

自检命令：

```
python3 scripts/main.py selftest
python3 -c "import json;r=json.load(open('/app/output/result.json',encoding='utf-8'));assert r.get('case_id') and r.get('status');print('ok',r['status'])"
```

## 7. 降级策略

- 缺数据/缺引文记录：状态降为 `insufficient_evidence` 或 `partial`，写 `pending_human`，不补造。
- 缺软件：脚本只用 Python 标准库；渲染工具不可用时 result.json 与 CSV 仍产出，SVG/预览缺省在 `limitations` 标 `preview_missing`。
- 接口限流/权限不足：记 `errors`/`degradations`，已获部分照常交付并标注边界。
- 任何中途异常：保留已落盘的 partial result.json，记 `errors`，进程不崩溃（bootstrap/finalize 一律退出码 0，或返回结构化错误而不留半截文件）。
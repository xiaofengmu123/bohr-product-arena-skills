---
name: citation-tracing
description: 引文追踪与关联文献发现（证据溯源强化版）。从种子论文出发，沿后向参考文献与前向施引文献多层级扩展，用共被引、文献耦合、作者/机构/数据集关联发现平行工作，完成去重与身份合并；每条结论与每条引用边都携带可回指的 locator 与逐字 quote，细粒度 evidence_map 支持独立复核。当任务要求“顺藤摸瓜”式文献追踪、引用网络构建、路径解释或论文身份对齐时使用。
version: 1.1.0
---

# 引文追踪与关联文献发现（证据溯源强化版）

## 1. 角色与适用场景
把“从种子论文追踪文献”沉淀为可复用流程与确定性工具。适用：前向/后向引文追踪、关联文献发现、引用网络解读、路径解释、去重与身份合并。不适用：普通关键词检索、全文阅读理解、引文语境内容分析、未经授权抓取。
判对标准只有三条：①扩展文献能否顺引用边说清来路；②每条边/每条结论是否有可回指的数据源与逐字依据；③去重合并后的清单是否干净到可直接进综述。

## 2. 输入契约
运行时输入固定为：
- `/input/case.json`：案例元数据，可能含 `case_id`、`output_contract`、种子论文信息与材料清单；
- `/input/task.md`：任务说明；
- `/input/` 下可能另有 PDF、JSON、CSV、TXT 材料。

不假设固定文件名/键名：先扫描目录，再按内容识别。文本按 `utf-8-sig`→`utf-8`→`latin-1` 容错读取；单文件 >2MB 跳过并记入 `degradations`。实际输入文件和原论文是唯一事实依据，不依据题面领域标签补造数据。

## 3. 输出契约
工作目录 `/app/work`，最终产物写 `/app/output/`。必产 `/app/output/result.json`（评分主产物）；辅助可复核产物 `report.md`、`papers.csv`、`edges.csv`、`citation_graph.svg` 由 `finalize` 生成。

`result.json` 必须包含：
- `case_id`；`status`（`ok|partial|insufficient_evidence|input_error`）；
- 题目 `output_contract` 全部字段（运行时从 `/input/case.json` 读取并原样回填键，缺值填 `null`/`[]`，不改名、不删未知键）；
- `seed`、`seed_key`、`research_question`；
- `results.papers`（每条含稳定 `id`、元数据与来源）、`results.edges`（有向引用边与关联边）、`results.clusters`、`results.paths`、`results.pruned`；
- `evidence_map`（细粒度证据，见 §6）、`sources`（每条带 `locator` 与逐字 `quote`）、`uncertainty`、`change_log`（每次修改的时间+内容+原因）、`limitations`、`pending_human`、`errors`、`degradations`、`runtime`。

输出原则：先写合法 partial 骨架再分析；任何异常都不留空文件；状态诚实，缺材料就 `insufficient_evidence`，不编造文献、数值、引用或分数。

## 4. 执行流程与时间预算
总预算 900 秒、64 次模型请求、单响应 ≤16384 token。**前 3 分钟内必须先落盘 result.json 骨架**，此后只对同一文件增量修改，每次修改都 append `change_log`。

- **Step 0（0–3 min，≤4 次请求）建骨架**：`python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work`。输出 `result.json` 骨架 + `/app/work/state.json`。此步必须写盘成功；即使写盘失败也要退出码 0 并留下最小合法 JSON。
- **Step 1（3–9 min，约 30 次请求）核实种子+一层扩展**：读取材料确认种子身份（DOI/arXiv/标题），写 `seed_key`；后向抽参考文献（种子 cites 出处），前向抽施引文献（谁 cites 种子）。每条边写 §5 边字段，证据逐字留档。
- **Step 2（9–13 min，约 20 次请求）多层扩展+去重+平行发现**：按需做第 2–3 层扩展并剪枝；用 `tool dedupe` 或 §5 键优先级做身份合并；用共被引/文献耦合/作者机构/数据集任务关联找不直接引用的平行工作并说明依据；识别奠基/桥接/热点节点。
- **Step 3（13–15 min，约 8 次请求）排序+路径+自检**：用 `tool rank` 排序（距离、被引、年份、来源置信度，不是“高被引=高相关”）；为关键远端文献用 `tool path` 写逐跳路径；核对评分维度清单；最后运行 `python3 scripts/main.py finalize --output /app/output`。预留最后 60 秒给 finalize 与校验；时间不足时先保证 result.json 合法，再补 artifacts。

## 5. 核心方法约定
- **边方向**：citation 边统一 `source` cites `target`；后向 = 种子为 source，前向 = 种子为 target；`direction` 记 `cites`。只知关联、不知谁引谁时记 `relation=associated`、`direction=undirected` 并写 `basis`（共被引/文献耦合/作者机构/数据集任务），不得猜方向。
- **身份合并优先级**：DOI → arXiv id → title+第一作者姓+年份 → title；合并证据写入 `merge_groups`（成员、键类型、键值）。
- **停止判据**：扩展层数默认 ≤3；按相关性/年份/文献类型剪枝；无新节点即停；剪枝原因写入 `results.pruned`。
- **排序**：距离、被引数、年份新鲜度、来源置信度加权，**不是“高被引=高相关”**。
- **确定性**：脚本固定排序键、固定布局，同输入两次运行产物一致。

## 6. 证据溯源（本版强化重点）
每个关键结论（论文身份、引用边、关联边、排序依据、剪枝、路径跳）都必须有唯一 `claim_id`，并在 `evidence_map.items` 中给 `evidence_id` + `locator`（如 `materials/x.pdf#p3`）+ 逐字 `quote`。字段：

```json
{"evidence_id":"ev-0001","claim_id":"c-0001","claim":"…结论…",
 "locator":"materials/seed.pdf#p1","quote":"…逐字原文…",
 "quote_sha256":"…","source_id":"src-0001",
 "source_role":"primary|secondary|metadata",
 "evidence_type":"identity|edge|association|ranking|pruning|path_hop|conflict",
 "provenance":"parsed_pdf|crossref|europepmc|author_material|inferred",
 "confidence":"high|medium|low","status":"verified|conflicted|unverified|not_reported",
 "conflicts":[]}
```

- `quote` 必须逐字来自材料，不得改写；`quote_sha256` 由脚本对 quote 计算（`python3 scripts/main.py tool quote-hash --text "…"`）。
- 冲突：同一指标两来源不一致，两者都记录并标 `source_conflict`，`conflicts` 互指；缺失标 `not_reported`。
- 每个 `source` 条目带 `locator` 与逐字 `quote`，指向该来源的原始片段。
- 详细字段语义与例句见 `references/evidence-spec.md`。

## 7. 自检清单（逐条勾选）
- [ ] 种子身份已在材料中核实并写 `seed_key`；
- [ ] 每条 citation 边方向正确、有来源证据，无猜测方向；
- [ ] 同文不同版本（预印本/会议版/期刊版/增补版）已合并，合并证据可复核；
- [ ] 关键被引出处、主要后续改进、代表性平行工作均已覆盖或注明遗漏；
- [ ] 每条结论都有 claim_id，且在 evidence_map 中有 locator + 逐字 quote；
- [ ] 去重与方向规范化由脚本完成，减少人工核验与重复翻页；
- [ ] 同输入两次运行脚本产物一致（确定性）；
- [ ] 跨源身份对齐、方向感知扩展、路径解释至少落地一项；
- [ ] 空输入/坏 JSON/坏编码不崩溃，退出码 0；
- [ ] 种子无引文记录、同名论文歧义、跨库字段冲突、撤稿与更正、限流或权限不足均已识别并处置。

## 8. 降级策略
- 缺数据/缺引文记录：状态降为 `insufficient_evidence` 或 `partial`，写 `pending_human`，不补造。
- 缺软件：脚本只用 Python 标准库；若渲染工具不可用，SVG 仍产出，PDF/PNG 预览缺省时在 `limitations` 标注 `preview_missing`。
- 接口限流/权限不足：记录到 `errors`/`degradations`，已获部分照常交付并标注边界。
- 任何中途异常：保留已落盘的 partial result.json，记录 `errors`，进程不崩溃。

## 9. 脚本调用
```bash
python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work
python3 scripts/main.py tool dedupe --papers /app/work/papers.json
python3 scripts/main.py tool rank --papers /app/work/papers.json --edges /app/work/edges.json --seed SEED_ID
python3 scripts/main.py tool path --edges /app/work/edges.json --start A --end B
python3 scripts/main.py tool quote-hash --text "逐字原文"
python3 scripts/main.py finalize --output /app/output
python3 scripts/main.py validate --output /app/output
python3 scripts/selftest.py
python3 -c "import json;r=json.load(open('/app/output/result.json',encoding='utf-8'));assert r.get('case_id') and r.get('status');print('ok',r['status'])"
```

交付前运行 `python3 scripts/selftest.py`（空目录/坏 JSON/缺输入/坏编码各一次），全部退出码 0。

---
name: citation-tracing
description: 引文追踪与关联文献发现。以种子论文为起点做前向/后向引文扩展、共被引与文献耦合关联、去重与身份合并，输出可复核的文献集、有向引用边、证据路径与诚实的不确定性/局限性清单。当任务要求顺藤摸瓜式文献检索、引用网络构建、路径解释或论文身份对齐时使用。
version: 1.0.0
---

# 引文追踪与关联文献发现

## 1. 角色与适用场景

本 Skill 把"从种子论文追踪文献"沉淀为确定性流程：沿后向参考文献找到方法与数据出处，沿前向施引找到后续改进与批评，再以共被引、文献耦合、作者/机构、数据集与任务关联发现不直接引用但同脉络的平行工作，最后做去重、身份合并、排序与路径解释。

适用：前向/后向引文追踪、关联文献发现、引用网络结构解读、路径解释、去重与身份合并。不适用：普通关键词检索、全文阅读理解、引文语境内容分析、未经授权的数据抓取。

判断是否做对只看三件事：扩展文献能否顺着引用边说清来路；每条边是否有可回指的数据源依据；去重合并后的清单是否干净到可直接进综述。**抓了多少条、调了几个接口都不是标准。**

## 2. 输入契约

运行时输入固定为：

- `/input/case.json`：案例元数据，可能含 `case_id`、`output_contract`、种子论文信息与材料清单；
- `/input/task.md`：任务说明；
- `/input/` 下另有 PDF、JSON、CSV、TXT 材料（可能位于子目录）。

不假设固定文件名或键名：先扫描目录，再按内容识别。文本按 `utf-8-sig` 优先容错读取（BOM/坏编码/坏 JSON 均降级不崩溃），单文件超 2MB 跳过并记 `degradations`。实际输入文件与原论文是唯一事实依据，不依据题面领域标签补造数据。

## 3. 输出契约

工作目录 `/app/work`，最终产物写 `/app/output/`。必须产出：

- `/app/output/result.json`（评分主产物，先写合法 partial 骨架再增量改进）；
- 辅助可复核产物：`report.md`、`papers.csv`、`edges.csv`、`citation_graph.svg`（由 `scripts/main.py finalize` 确定性生成）。

`result.json` 必须包含：

- `case_id`；
- 题目 `output_contract` 的全部字段（运行时从 `/input/case.json` 读取并原样回填键，缺值填 `null`/`[]`，不改名、不删未知键）；
- `status`：`ok | partial | insufficient_evidence | input_error`；
- `seed` / `seed_key` / `research_question`；
- `sources`（每条带 `locator` 与逐字 `quote`）、`uncertainty`、`limitations`、`pending_human`、`errors`、`degradations`；
- `change_log`（每次修改的时间 + 内容 + 原因）；
- `results.papers`（每条含稳定 `id`、元数据、`relevance_class`、`confidence`、`evidence`）、`results.edges`（统一 `source` cites `target` 的有向边）、`results.clusters`、`results.paths`、`results.pruned`；
- `runtime`（起止时间、时长、输入文件清单、工具版本）。

输出原则：先写合法 partial 骨架再分析，任何异常都不留空文件；状态诚实，缺材料就 `insufficient_evidence`，不编造文献、数值、引用或分数。

## 4. 执行流程与时间预算

总预算 **900 秒、64 次模型请求**。**前 3 分钟内必须先落盘 result.json 骨架**，此后只对同一文件增量修改，每次修改都 append `change_log`。

- **Step 0（0–3 min，≤4 请求）— 建骨架**：`python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work`。输入 `/input`，输出 `result.json` 骨架 + `/app/work/state.json`。此步必须写盘成功；即使写盘失败也退出码 0 并留最小合法 JSON。
- **Step 1（3–9 min，约 30 请求）— 核实种子 + 一层扩展**：读取材料，核实种子身份（DOI/arXiv/标题），用 `scripts/main.py record --kind papers` 写入 `seed_key`；后向抽参考文献（种子 cites 出处），前向抽施引文献（谁 cites 种子）。每条边写 `source/target/relation=cites/direction/confidence/evidence`，证据逐字留档。
- **Step 2（9–13 min，约 20 请求）— 多层扩展 + 去重 + 平行发现**：按需做第 2–3 层扩展并剪枝；`scripts/main.py dedupe` 做身份合并；用共被引/文献耦合/作者机构/数据集任务关联找不直接引用的平行工作并说明依据；识别奠基/桥接/热点节点。
- **Step 3（13–15 min，约 8 请求）— 排序 + 路径 + 自检**：`scripts/main.py rank` 排序（距离、被引、年份、来源置信度，不是"高被引=高相关"）；对关键远端文献 `scripts/main.py paths --from ID --to ID` 写逐跳路径；最后 `scripts/main.py finalize --output /app/output --work /app/work` 生成报告/CSV/SVG 并校验。预留最后 60 秒给 finalize；时间不足时先保证 result.json 合法，再补 artifacts。

## 5. 核心方法约定

- **边方向**：统一 `source` cites `target`。后向 = 种子为 source；前向 = 种子为 target。外部数据只给被引列表不给方向时标 `direction_unknown`，不得猜测。非引用关联边用 `relation=associates/cocites/couples` + `direction=parallel`。
- **身份合并优先级**：DOI → arXiv id → 标题+第一作者姓+年份 → 标题；保留合并证据（成员、键类型、键值，写入 `merged_from` 与 `merge_log`）。
- **排序**：显式 `rank` 优先，否则 `rank_score = 相关性类别权重 − 距离惩罚 + 置信度加分 + 年份轻微加成`；高被引不等于高相关。
- **停止判据**：扩展层数上限默认 3；按相关性/年份/文献类型剪枝；无新节点即停。剪枝原因写入 `results.pruned`。
- **证据**：每个关键结论/边/数值给 `locator`（如 `materials/x.pdf#p3`）+ 逐字 `quote`；冲突信息两者都记并标 `source_conflict`；缺失标 `not_reported`。

## 6. 不确定性与局限性显式化（本版重点）

不把"没找到"悄悄埋进结论。任何缺口都落到三类显式字段：

- `uncertainty[]`：每条含 `type`（`coverage_gap | source_conflict | direction_unknown | unverified | missing_metadata | retraction_correction`）、`subject`、`detail`、`affects`（影响哪些结论）、`resolution`（如何处理）。
- `limitations[]`：数据源覆盖范围、接口能力边界、软件缺省（如 `preview_missing`）、无法核验的假设。
- `pending_human[]`：需要人工复核/补全的事项。

规则：新预印本无前向引用记录、同名论文歧义、跨库字段冲突、撤稿与更正、限流或权限不足，都必须识别并写入上述字段，**不得用缺省值冒充已完成**。

## 7. 自检清单

逐条勾选，对应评分维度：

- [ ] 实际效果：种子身份已在材料中核实并写 `seed_key`；
- [ ] 实际效果：每条边方向正确、有来源证据，无猜测方向；
- [ ] 实际效果：同文不同版本（预印本/会议版/期刊版/增补版）已合并，合并证据可复核；
- [ ] 实际效果：关键被引出处、主要后续改进、代表性平行工作均已覆盖或注明遗漏；
- [ ] 使用增益：去重与方向规范化由脚本完成，减少人工核验与重复翻页；
- [ ] 使用增益：同输入两次运行 finalize 产物字节一致（脚本确定性）；
- [ ] 创新增益：跨源身份对齐、方向感知扩展、路径解释至少落地一项；
- [ ] 可靠体验：空输入/坏 JSON/坏编码不崩溃，退出码 0；
- [ ] 可靠体验：种子无引文记录、同名歧义、跨库冲突、撤稿更正、限流/权限不足均已识别处置；
- [ ] 生态贡献：文档齐备、边表字段与去重规则可复用。

自检命令：

```bash
python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work
python3 scripts/main.py finalize --output /app/output --work /app/work
python3 scripts/main.py selftest
python3 -c "import json; r=json.load(open('/app/output/result.json',encoding='utf-8')); assert r.get('case_id') and r.get('status'); print('ok', r['status'])"
```

## 8. 降级策略

- 缺数据/缺引文记录：状态降为 `insufficient_evidence` 或 `partial`，写 `pending_human` 与 `uncertainty`，不补造。
- 缺软件：脚本只用 Python 标准库；渲染工具不可用时 SVG 仍产出，缺 PDF/PNG 预览在 `limitations` 标 `preview_missing`。
- 接口限流/权限不足：记 `errors`/`degradations`，已获部分照常交付并标边界。
- 任何中途异常：保留已落盘 partial result.json，记录 `errors`，进程不崩溃、退出码 0。

## 9. 脚本调用参考

```bash
python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work
python3 scripts/main.py record --kind papers --json-file /app/work/papers.json --reason "第1层后向"
python3 scripts/main.py record --kind edges --json '[{"id":"e1","source":"seed","target":"p1","relation":"cites","direction":"backward","confidence":"confirmed","evidence":[{"locator":"materials/seed.pdf#p3","quote":"..."}]}]'
python3 scripts/main.py dedupe --output /app/output
python3 scripts/main.py rank --output /app/output
python3 scripts/main.py paths --from seed --to p9 --id path-1 --output /app/output
python3 scripts/main.py finalize --output /app/output --work /app/work
```

`--json` 接受内联 JSON（单引号包裹），也可用 `--json-file` 指向文件。脚本仅依赖标准库，路径均相对本包，可复现、可复核。

---
name: citation-tracing
description: 引文追踪与关联文献发现。以种子论文为起点，沿后向参考文献与前向施引文献做多层级扩展，用共被引、文献耦合、作者或数据集关联发现平行工作，完成去重与身份合并，输出可复核的文献集、有向引用边、证据路径与诚实状态。内置确定性脚本与自检，保证同一输入两次运行产物一致。当任务要求“顺藤摸瓜”式文献检索、引文网络构建、路径解释或论文身份对齐时使用。
version: 2.0.0
---

# 引文追踪与关联文献发现

## 1. 角色与何时使用

本 Skill 把“从种子论文追踪文献”沉淀为可复用的确定性流程。适用：前向/后向引文追踪、关联文献发现、引用网络结构解读、路径解释、去重与身份合并。不适用：普通关键词检索、全文阅读理解、引文语境内容分析、未经授权的数据抓取。判断是否做对只看三件事：扩展文献能否顺着引用边说清来路；每条边是否有可回指的数据源依据；去重合并后的清单是否干净到可直接进综述。

## 2. 输入契约

运行时输入固定为：

- `/input/case.json`：案例元数据，可能含 `case_id`、`output_contract`、种子论文信息与材料清单；
- `/input/task.md`：任务说明；
- `/input/` 下可能另有 PDF、JSON、CSV、TXT 材料。

不假设固定文件名或键名：先扫描目录，再按内容识别。文本按 `utf-8-sig` 优先容错读取，坏编码降级，单文件超过 2MB 跳过并记 `degradations`。实际输入文件和原论文是唯一事实依据，不依据题面领域标签补造数据。

## 3. 输出契约

工作目录 `/app/work`，最终产物写 `/app/output/`。必须产出：

- `/app/output/result.json`（评分主产物）；
- 辅助可复核产物：`report.md`、`papers.csv`、`edges.csv`、`citation_graph.svg`（由 `finalize` 确定性生成）。

`result.json` 必须包含：

- `case_id`；
- 题目 `output_contract` 的全部字段（运行时从 `/input/case.json` 读取并原样回填键，缺值填 `null`/`[]`，不改名、不删未知键）；
- `status`：`ok | partial | insufficient_evidence | input_error`；
- `sources`（每条带 `locator` 与逐字 `quote`）、`uncertainty`（冲突/缺失/覆盖缺口）、`change_log`（每次修改的时间 + 内容 + 原因）；
- `results.papers`（每条含稳定 `id`、元数据与来源）、`results.edges`（统一 `source` cites `target` 的有向边）、`results.clusters`、`results.paths`；
- 另加 `seed`、`seed_key`、`research_question`、`limitations`、`pending_human`、`errors`、`degradations`、`runtime`。

输出原则：先写合法 partial 骨架再分析，任何异常都不留空文件；状态诚实，缺材料就 `insufficient_evidence`，不编造文献、数值、引用或分数。字段规范见 `references/edge-schema-and-dedupe.md`。

## 4. 执行流程与时间预算

总预算 900 秒、64 次模型请求。**前 3 分钟内必须先落盘 result.json 骨架**，此后只对同一文件增量修改，每次修改都 append `change_log`。

Step 0（0–3 min，≤4 次请求）——建骨架：

```bash
python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work
```

输入：`/input` 目录。输出：`/app/output/result.json` 骨架 + `/app/work/state.json`。此步必须写盘成功；即使写盘失败也要退出码 0 并留下最小合法 JSON。

Step 1（3–9 min，约 30 次请求）——核实种子 + 一层扩展：读取材料，确认种子身份（DOI/arXiv/标题），写入 `seed_key`；后向抽参考文献（种子 cites 出处），前向抽施引文献（谁 cites 种子）。每条边写 `source/target/relation=cites/source_label/target_label/evidence`，证据逐字留档。

Step 2（9–13 min，约 20 次请求）——多层扩展 + 去重 + 平行发现：按需做第 2–3 层扩展并剪枝；用 `dedupe_papers` 做身份合并；用共被引/文献耦合/作者机构/数据集任务关联找不直接引用的平行工作并说明依据；识别奠基/桥接/热点节点。

Step 3（13–15 min，约 8 次请求）——排序 + 路径 + 自检：用 `rank_papers` 排序（距离、被引、年份、来源置信度，不是“高被引=高相关”）；为关键远端文献用 `shortest_path` 写逐跳路径；核对自检清单；最后运行：

```bash
python3 scripts/main.py finalize --output /app/output --work /app/work
```

生成报告、CSV、SVG 并校验 result.json。预留最后 60 秒给 finalize 与校验；时间不足时先保证 result.json 合法，再补 artifacts。

## 5. 核心方法约定

- 边方向：统一 `source` cites `target`。后向 = 种子为 source；前向 = 种子为 target。外部数据只给被引列表不给方向时，标 `direction_unknown`，不得猜测。
- 身份合并优先级：DOI → arXiv id → 标题 + 第一作者姓 + 年份 → 标题 + 年份 → 标题近似；保留合并证据（成员、键类型、键值）。规则见 `references/edge-schema-and-dedupe.md`。
- 停止判据：扩展层数上限默认 3；按相关性/年份/文献类型剪枝；无新节点即停。剪枝原因写入 `results.pruned`。
- 证据：每个关键结论/边/数值给 `locator`（如 `materials/x.pdf#p3`）+ 逐字 `quote`；冲突信息两者都记并标 `source_conflict`；缺失标 `not_reported`。
- 确定性：排序键、JSON 序列化、SVG 布局均固定；`content_hash()` 只对排除 `runtime`/`change_log` 后的确定性子集取 SHA-256，保证同输入两次产物一致。

## 6. 自检清单

逐条勾选，对应评分维度：

- [ ] 实际效果：种子身份已在材料中核实并写 `seed_key`；
- [ ] 实际效果：每条边方向正确、有来源证据，无猜测方向；
- [ ] 实际效果：同文不同版本（预印本/会议版/期刊版/增补版）已合并，合并证据可复核；
- [ ] 实际效果：关键被引出处、主要后续改进、代表性平行工作均已覆盖或注明遗漏；
- [ ] 使用增益：去重与方向规范化由脚本完成，减少人工核验与重复翻页；
- [ ] 使用增益：同输入两次运行产物一致（`content_hash` 与 CSV/SVG 逐字节一致）；
- [ ] 创新增益：跨源身份对齐、方向感知扩展、路径解释至少落地一项；
- [ ] 可靠体验：空输入/坏 JSON/坏编码不崩溃，退出码 0；
- [ ] 可靠体验：种子无引文记录、同名论文歧义、跨库字段冲突、撤稿与更正、限流或权限不足均已识别并处置；
- [ ] 生态贡献：文档齐备、边表字段与去重规则可复用。

自检命令（不访问网络）：

```bash
python3 scripts/main.py selftest
```

## 7. 降级策略

- 缺数据/缺引文记录：状态降为 `insufficient_evidence` 或 `partial`，写 `pending_human`，不补造。
- 缺软件：脚本只用 Python 标准库；若环境渲染工具不可用，SVG 仍产出，PDF/PNG 预览缺省时在 `limitations` 标注 `preview_missing`。
- 接口限流/权限不足：记录到 `errors`/`degradations`，已获部分照常交付并标注边界。
- 任何中途异常：保留已落盘的 partial result.json，记录 `errors`，进程不崩溃。

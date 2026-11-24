---
name: citation-tracing
description: 引文追踪与关联文献发现。从种子论文出发，沿后向参考文献与前向施引文献做多层级扩展，用共被引、文献耦合、作者或数据集关联发现平行工作，完成论文身份去重合并，输出可复核的文献集、有向引用边、证据路径与诚实状态；配套脚本提供确定性骨架落盘、去重、结构排序、最短路径与 CSV/SVG 报告。当任务要求顺藤摸瓜式文献检索、引文网络构建、路径解释或论文身份对齐时使用。
version: 1.1.0
---

# 引文追踪与关联文献发现

## 1. 角色与适用场景

把「从种子论文追踪文献」沉淀为可复用、可复核的流程。适用：前向/后向引文追踪、关联文献发现、引用网络结构解读、路径解释、去重与身份合并。不适用：普通关键词检索、全文阅读理解、引文语境内容分析、未经授权的数据抓取。

做对与否只看三件事：扩展文献能否顺着引用边说清来路；每条边是否有可回指的数据源依据；去重合并后的清单是否干净到可直接进综述。

## 2. 输入契约

运行时输入固定（先扫描目录再识别内容，不假设文件名/键名）：

- `/input/case.json`：案例元数据，含 `case_id` 与 `output_contract`（字段需原样回填）；
- `/input/task.md`：任务说明；
- `/input/` 下另可能有 PDF、JSON、CSV、TXT 材料。

文本按 `utf-8-sig` 容错读取，坏编码降级，单文件 > 2MB 跳过并记 `degradations`。实际输入文件与原论文是唯一事实依据，不依据题面领域标签补造数据。

## 3. 输出契约

工作目录 `/app/work`，最终产物写 `/app/output/`。**必须先落盘合法 partial 骨架再分析**，之后只做增量改进。主产物：

- `/app/output/result.json`（评分主产物）；
- 辅助可复核产物：`report.md`、`papers.csv`、`edges.csv`、`citation_graph.svg`（由脚本 `finalize` 生成）。

`result.json` 必含：`case_id`；`status`（`ok|partial|insufficient_evidence|input_error`）；题目 `output_contract` 全部字段（运行时原样回填键，缺值填 `null`/`[]`，不改名、不删未知键，另存 `output_contract` 快照）；`sources`（每条带 `locator` 与逐字 `quote`）、`uncertainty`（冲突/缺失/覆盖缺口）、`change_log`（每次修改的时间+内容+原因）；`results.papers`（稳定 `id`+元数据+来源）、`results.edges`（统一 `source` cites `target`）、`results.clusters`、`results.paths`；`seed`、`seed_key`、`research_question`、`limitations`、`pending_human`、`errors`、`degradations`、`runtime`。

## 4. 执行流程与时间预算

总预算 **900 秒、64 次模型请求**。**前 3 分钟内（≤4 次请求）必须先落盘 result.json 骨架**，之后只对同一文件增量修改，每次修改 append `change_log`。

- **Step 0（0–3 min）** 建骨架：`python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work`（Skill 安装在 `/skills/weekly-skill`，即 `/skills/weekly-skill/scripts/main.py`）。任何异常也必须退出码 0 并留下最小合法 JSON。
- **Step 1（3–9 min）** 核实种子 + 一层扩展：确认种子身份（DOI/arXiv/标题）写入 `seed_key`；后向抽参考文献（种子 cites 出处）、前向抽施引文献（谁 cites 种子）。每条边写 `source/target/relation=cites/source_label/target_label/evidence_locator/evidence_quote`。
- **Step 2（9–13 min）** 多层扩展 + 去重 + 平行发现：按需第 2–3 层并剪枝；`python3 scripts/main.py dedupe --papers … --out …` 做身份合并；用共被引/文献耦合/作者机构/数据集任务关联找不直接引用的平行工作并写依据；识别奠基/桥接/热点节点。
- **Step 3（13–15 min）** 排序 + 路径 + 自检：`python3 scripts/main.py rank --papers … --edges … --seed …` 结构排序（距离、被引、年份、来源置信度，不是"高被引=高相关"）；关键远端文献用 `python3 scripts/main.py path --edges … --source … --target …` 写逐跳路径；核对自检清单；最后 `python3 scripts/main.py finalize --output /app/output` 生成报告/CSV/SVG 并校验。预留最后 60 秒给 finalize；时间不足先保证 result.json 合法。

## 5. 核心方法约定

- **边方向**：统一 `source` cites `target`。后向=种子为 source；前向=种子为 target。外部只给被引列表不给方向 → 标 `direction_unknown`，不得猜测。
- **身份合并优先级**：DOI → arXiv id → title+一作姓+年份 → title；保留合并证据（`key_type`/`key_value`/`member_id`）。见 `references/rules.md` §2。
- **证据**：每个关键结论/边/数值给 `locator`（如 `materials/x.pdf#p3`）+ 逐字 `quote`；冲突两者都记标 `source_conflict`；缺失标 `not_reported`。
- **停止判据**：层数默认 3、相关性/年份/类型剪枝、无新节点即停；剪枝原因写入 `results.pruned`。

## 6. 自检清单（交付前逐条勾选，对应评分维度）

- [ ] 实际效果——种子身份已在材料中核实并写 `seed_key`
- [ ] 实际效果——每条边方向正确、有来源证据，无猜测方向
- [ ] 实际效果——同文不同版本已合并，合并证据可复核（`merge_evidence`/`clusters`）
- [ ] 实际效果——关键被引出处、主要后续改进、代表性平行工作已覆盖或注明遗漏
- [ ] 使用增益——去重与方向规范化由脚本完成，减少人工核验与重复翻页
- [ ] 使用增益——同一输入重复 finalize 产物一致（脚本确定性，见 `scripts/selftest.py`）
- [ ] 创新增益——跨源身份对齐、方向感知扩展、路径解释至少落地一项
- [ ] 可靠体验——空输入/坏 JSON/坏编码不崩溃，退出码 0
- [ ] 可靠体验——已识别并处置：种子无引文记录、同名歧义、跨库冲突、撤稿更正、限流/权限不足
- [ ] 生态贡献——文档/边表字段/去重规则可复用（`references/rules.md`）

自检命令：

```
python3 -B scripts/main.py --help
python3 -B scripts/main.py bootstrap --input /input --output /app/output --work /app/work
python3 -B scripts/main.py finalize --output /app/output
python3 -B scripts/selftest.py
python3 -c "import json; r=json.load(open('/app/output/result.json',encoding='utf-8')); assert r.get('case_id') and r.get('status'); print('ok', r['status'])"
```

## 7. 降级策略

- **缺数据/缺引文记录**：状态降为 `insufficient_evidence` 或 `partial`，事项写入 `pending_human`，不补造文献、数值或引用。
- **缺软件**：脚本只用标准库；若 SVG 渲染不可用仍产出 SVG，PDF/PNG 缺省时在 `limitations` 标 `preview_missing`。
- **接口限流/权限不足**：记录到 `errors`/`degradations`，已获部分照常交付并标注边界。
- **中途异常**：保留已落盘 partial result.json，记录 `errors`，进程不崩溃。

## 8. 脚本调用

脚本位于本 Skill 包 `scripts/`（安装后即 `/skills/weekly-skill/scripts/`），仅依赖标准库、路径相对。薄入口 `main.py`，实现 `core_impl.py`，自检 `selftest.py`。最小示例（示意输出须标注为示例，不冒充正式结果）：

```
python3 scripts/main.py bootstrap --input ./demo/input --output ./demo/output --work ./demo/work
python3 scripts/main.py dedupe   --papers ./in_papers.json --out ./out_dedupe.json
python3 scripts/main.py rank     --papers ./in_papers.json --edges ./in_edges.json --seed A --out ./out_rank.json
python3 scripts/main.py path     --edges ./in_edges.json --source A --target Z --out ./out_path.json
python3 scripts/main.py finalize --output ./demo/output
python3 scripts/selftest.py
```
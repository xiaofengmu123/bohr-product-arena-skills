---
name: citation-tracing
description: 引文追踪与关联文献发现。从种子论文出发，沿后向参考文献与前向施引文献做多层级扩展，用共被引、文献耦合、作者或数据集关联等信号发现平行工作，完成去重与身份合并，输出可复核的文献集、有向引用边、证据路径与诚实状态。当任务要求"顺藤摸瓜"式文献检索、引文网络构建、路径解释或论文身份对齐时使用。
version: 1.1.0
---

# 引文追踪与关联文献发现（评分维度对齐版）

## 1. 角色与何时使用

本 Skill 把"从种子论文追踪文献"沉淀为可复用的确定性流程。适用：前向/后向引文追踪、关联文献发现、引用网络结构解读、路径解释、去重与身份合并。不适用：普通关键词检索、全文阅读理解、引文语境内容分析、未经授权的数据抓取。

判定是否做对只看三件事：扩展文献能否顺着引用边说清来路；每条引用边是否有可回指的数据源依据；去重合并后清单是否干净到可直接进综述。实际输入文件和原论文是唯一事实依据，不依据错误的领域标签补造数据。

## 2. 输入契约

运行时输入固定为：

- `/input/case.json`：案例元数据，可能含 `case_id`、`output_contract`、种子论文信息与材料清单；
- `/input/task.md`：任务说明；
- `/input/` 下可能另有 PDF、JSON、CSV、TXT 材料。

不假设固定文件名或键名：先扫描目录，再按内容识别。文本按 `utf-8-sig` 优先容错读取，坏编码降级，单文件 >2MB 跳过并记 `degradations`。PDF 优先读平台解析文本，无解析层再用 `pdftotext`；只读实际文件，不依赖题面标签假设内容。

## 3. 输出契约

工作目录 `/app/work`，最终产物写 `/app/output/`。必须产出：

- `/app/output/result.json`（评分主产物）；
- 辅助可复核产物：`report.md`、`papers.csv`、`edges.csv`、`citation_graph.svg`（由 `scripts/main.py finalize` 确定性生成）。

`result.json` 必须包含：

- `case_id`；
- 题目 `output_contract` 的全部字段（运行时从 `/input/case.json` 读取并原样回填键，缺值填 `null`/`[]`，不改名、不删未知键；同时保留 `output_contract` 原始副本）；
- `status`：`ok | partial | insufficient_evidence | input_error`；
- `results.papers`（每条含稳定 `id`、元数据与来源、`level`、`confidence`）、`results.edges`（统一 `source` cites `target` 的有向边，关系为 `cites` 或显式 `direction_unknown`）、`results.clusters`、`results.paths`、`results.pruned`、`results.ranking`、`results.papers_pre_merge` 与 `results.id_remap`（仅发生合并时写入）；
- `sources`（每条带 `locator` 与逐字 `quote`）、`uncertainty`（冲突/缺失/覆盖缺口）、`change_log`（每次修改的时间 + 内容 + 原因）；
- 另加 `seed`、`seed_key`、`research_question`、`limitations`、`pending_human`、`errors`、`degradations`、`runtime`。

输出原则：先写合法 partial 骨架再分析，任何异常都不留空文件；状态诚实，缺材料就 `insufficient_evidence`，不编造文献、数值、引用或分数。

## 4. 执行流程与时间预算

总预算 900 秒、64 次模型请求、单响应含推理 ≤16384 tokens。**前 3 分钟内必须先落盘 result.json 骨架**，此后只对同一文件增量修改，每次修改都 append `change_log`。

**Step 0（0–3 min，≤4 次请求）——建骨架**：

```
python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work
```

输入 `/input`，输出 `/app/output/result.json` 骨架 + `/app/work/state.json`（含扫描清单）。此步必须写盘成功；即使写盘失败也要退出码 0 并留下最小合法 JSON。骨架落盘后立即把 `status` 置为 `partial` 或 `insufficient_evidence`。

**Step 1（3–9 min，约 30 次请求）——核实种子 + 一层扩展**：读取材料，确认种子身份（DOI/arXiv/标题），用 `record --kind seed` 写 `seed_key`；后向抽参考文献（种子 cites 出处）、前向抽施引文献（谁 cites 种子）。每条边用 `record --kind edge` 追加，写 `source/target/relation=cites/source_label/target_label/evidence`，证据逐字留档。

**Step 2（9–13 min，约 20 次请求）——多层扩展 + 去重 + 平行发现**：按需做第 2–3 层扩展并剪枝；用 `scripts/main.py rank` 与 `finalize` 内的确定性 `dedupe` 做身份合并；用共被引/文献耦合/作者机构/数据集任务关联找不直接引用的平行工作并说明依据；识别奠基/桥接/热点节点并写入 `clusters`/`paths`。

**Step 3（13–15 min，约 8 次请求）——排序 + 路径 + 自检**：用 `scripts/main.py rank` 确定性排序（距离优先，被引数/年份/来源置信度为次要信号，不是"高被引=高相关"）；对关键远端文献用 `scripts/main.py path` 写逐跳路径；核对第 6 节自检清单；最后运行：

```
python3 scripts/main.py finalize --output /app/output
```

生成报告、CSV、SVG 并校验 result.json。预留最后 60 秒给 finalize 与校验；时间不足时先保证 result.json 合法，再补 artifacts。

## 5. 核心方法约定

- **边方向**：统一 `source` cites `target`。后向 = 种子为 source；前向 = 种子为 target。外部数据只给被引列表不给方向时，标 `direction_unknown`，不得猜测。
- **身份合并**：优先级 DOI → arXiv → title + 第一作者姓 + 年份 → title。前两类为强键、第三类为中键，均联合并；title-only 只记 `uncertainty` 候选，不自动合并（避免同名不同文误合并）。合并证据含成员、键类型、键值与 `id_remap`，写入 `results.papers_pre_merge` + `results.id_remap`。
- **停止判据**：扩展层数上限默认 3；按相关性/年份/文献类型剪枝；无新节点即停。剪枝原因写 `results.pruned`。
- **证据**：每个关键结论/边/数值给 `locator`（如 `material.pdf#p3` 或 `case.json#seed`）+ 逐字 `quote`；冲突信息两者都记并标 `source_conflict`；缺失标 `not_reported`。

## 6. 评分维度对齐与自检清单（交付前逐条勾选）

**实际效果 40 分**：

- [ ] 任务完成度：以种子为起点、沿引用与关联完成扩展，产出可直接使用的文献集与证据（对应 `results.papers/edges/clusters/paths`）；
- [ ] 结果正确性：文献真实存在；边方向正确；同文不同版本已合并且证据可复核；关联文献确有方法或主题依据；
- [ ] 结果完整性：覆盖种子关键被引出处、主要后续改进、代表性平行工作，遗漏写入 `limitations`；
- [ ] 结果可核验性：`seed`/`seed_key`、`level`（扩展层级）、每条边的 `evidence.locator`+`quote`、数据源信息均可独立复核。

**使用增益 25 分**：

- [ ] 质量增益：同一预算下比裸检索多产出方向规范化、去重与证据链三层保证；
- [ ] 效率增益：去重、方向规范化、排序、路径解释由脚本确定性完成，减少人工核验条数与无效翻页；
- [ ] 增益可验证性：`scripts/selftest.py` 在同一 demo 上跑通完整链路并打印通过/失败项，可复现对照。

**创新增益 15 分**：

- [ ] 新增能力：跨源身份对齐（并查集多键）、方向感知扩展（`--reverse` 前向追踪）、可解释逐跳路径至少落地一项；
- [ ] 方法改进：扩展层数上限 + 剪枝原因 + 停止判据、去重键优先级、路径 BFS 与排序"距离优先"构成实质取舍；
- [ ] 创新证据：`rank`/`path` 输出自带 `note` 说明"非高被引=高相关"等边界，可复核方法价值。

**可靠体验 10 分**：

- [ ] 调用与易用性：`bootstrap → record → finalize` 三步即可完成一次从种子到文献集的追踪；
- [ ] 运行稳定性：脚本只用标准库、确定性排序、固定布局，同输入两次运行产物一致；
- [ ] 异常处理：空输入/坏 JSON/坏编码不崩溃、退出码 0；种子无引文记录、同名歧义、字段冲突、撤稿更正、限流权限均已识别并逐条记录。

**生态贡献 10 分**：

- [ ] 文档完整性：SKILL.md 写清用途、调用、输入输出、异常与维护信息；
- [ ] 资源可复用：`papers.csv`/`edges.csv` 字段稳定、去重脚本与自检脚本可直接复用；
- [ ] 分享价值：边表字段、去重规则、路径图沉淀为可分享的追踪模板。

自检命令（三连，均退出码 0）：

```
python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work
python3 scripts/selftest.py
python3 scripts/main.py finalize --output /app/output
python3 -c "import json; r=json.load(open('/app/output/result.json',encoding='utf-8')); assert r.get('case_id') and r.get('status'); print('ok', r['status'])"
```

## 7. 降级策略

- 缺数据/缺引文记录：状态降为 `insufficient_evidence` 或 `partial`，写 `pending_human` 与 `limitations`，不补造。
- 缺软件：脚本只用 Python 标准库；管理端环境若 SVG 之外还需 PDF/PNG 预览，缺省时在 `limitations` 标 `preview_missing`。
- 接口限流/权限不足：记录到 `errors`/`degradations`，已获部分照常交付并标注边界。
- 任何中途异常：保留已落盘的 partial result.json，记录 `errors`，进程不崩溃（`main.py` 兜底退出码 0）。

## 8. 最小调用示例（示例，非正式案例）

```bash
python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work
python3 scripts/main.py record --kind seed --json '{"seed":"p1","seed_key":"10.1000/xyz","research_question":"该方法的原始出处"}'
python3 scripts/main.py record --kind paper --json '{"id":"p1","title":"Example","year":2020,"doi":"10.1000/xyz","level":0}'
python3 scripts/main.py record --kind edge --json '{"source":"p1","target":"p0","relation":"cites","evidence":[{"locator":"x.pdf#p3","quote":"...we build on p0..."}]}'
python3 scripts/main.py path --source p1 --target p0 --output /app/output
python3 scripts/main.py finalize --output /app/output
```

示意输出均标注为示例，不得冒充正式案例结果。
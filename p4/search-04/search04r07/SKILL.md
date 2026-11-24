---
name: citation-tracing
description: 引文追踪与关联文献发现。以种子论文为起点，沿后向参考文献与前向施引文献做多层级扩展，用共被引、文献耦合、作者/数据集/任务关联等信号发现平行工作，完成身份合并与去重，输出可复核的文献集、有向引用边、证据路径与诚实状态。鲁棒性优先：空输入、坏 JSON、缺参数、超大文件均优雅降级不崩溃。当任务要求“顺藤摸瓜”式文献检索、引用网络构建、路径解释或论文身份对齐时使用。
version: 2.0.0
---

# 引文追踪与关联文献发现

## 1. 角色与何时使用

本 Skill 将“从种子论文追踪文献”沉淀为可复用的流程与确定性工具。适用：前向/后向引文追踪、多层级扩展、关联文献发现、引用网络解读、路径解释、去重与身份合并。不适用：普通关键词检索、全文阅读理解、引文语境内容分析、未经授权的数据抓取。

判断做对只看三件事：扩展出的文献能否顺着引用边说清来路；每条边是否有可回指的数据源依据；去重合并后的清单是否干净到可直接进综述。

## 2. 输入契约

运行时输入固定为 `/input/case.json`（含 `case_id`、`output_contract`、种子论文信息与材料清单）、`/input/task.md`，`/input/` 下可能另有 PDF/JSON/CSV/TXT/BibTeX 材料。**先扫描目录再按内容识别，不假设固定文件名或键名**；文本按 `utf-8-sig → utf-8 → utf-16 → latin-1` 顺序容错读取，单文件超过 2MB 跳过并记入 `degradations`。实际输入文件和原论文是唯一事实依据，不依据题面领域标签补造数据。

## 3. 输出契约

工作目录 `/app/work`，最终产物写 `/app/output/`。必须产出 `/app/output/result.json`（评分主产物），并尽量产出 `report.md`、`papers.csv`、`edges.csv`、`citation_graph.svg`。

`result.json` 必须包含：

- `case_id`；题目 `output_contract` 的**全部字段**（运行时从 `/input/case.json` 读取并原样回填键，缺值填 `null`/`[]`，不改名、不删未知键，嵌套对象递归展开）；
- `status`：`ok | partial | insufficient_evidence | input_error`；
- `sources`（每条带 `locator` + 逐字 `quote`）、`uncertainty`（冲突/缺失/覆盖缺口）、`change_log`（每次修改：`at` 时间 + `what` 内容 + `why` 原因）；
- 主产物字段（依 contract 而定，通常为 `results.papers`、`results.edges`、`results.clusters`、`results.paths`）；
- 另加 `seed`、`seed_key`、`research_question`、`pending_human`、`errors`、`degradations`、`limitations`、`runtime_seconds`。

输出原则：**先写合法 partial 骨架再分析**，任何异常都不留空文件；状态诚实，缺材料就 `insufficient_evidence`，不编造文献、数值、引用或分数。

## 4. 执行流程与时间预算

总预算 **900 秒、64 次模型请求**，单次响应含推理 ≤16384 token。**前 3 分钟内必须落盘一版 `result.json` 骨架**，此后只对同一文件增量修改，每次修改 append `change_log`。

**Step 0（0–3 min，≤4 次请求）——落盘骨架：**

```bash
python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work
```

此步必须写盘成功；即使写盘失败也要退出码 0 并留下最小合法 JSON。

**Step 1（3–9 min，约 30 次请求）——核实种子 + 一层扩展：** 读取材料，确认种子身份（DOI/arXiv/标题），写 `seed_key`；后向抽参考文献（种子 cites 出处），前向抽施引文献（谁 cites 种子）。每条边写 `source/target/relation=cites/source_label/target_label/evidence`，证据逐字留档（`evidence.locator` + `evidence.quote`）。

**Step 2（9–13 min，约 20 次请求）——多层扩展 + 去重 + 平行发现：** 按需做第 2–3 层扩展并剪枝；把候选写入 `/app/work/papers.json`、`/app/work/edges.json` 后用 `python3 scripts/main.py dedupe --work /app/work` 做身份合并与边端点重映射（产物 `papers_deduped.json`/`edges_deduped.json`/`merges.json`）；用共被引/文献耦合/作者机构/数据集任务关联找不直接引用的平行工作并说明依据；识别奠基/桥接/热点节点。

**Step 3（13–15 min，约 8 次请求）——排序 + 路径 + 自检：** 用 `citation_lib.rank_papers` 排序（距离、被引、年份、来源置信度，非“高被引=高相关”）；为关键远端文献用 `citation_lib.shortest_paths` 写逐跳路径；核对评分维度；最后运行：

```bash
python3 scripts/main.py finalize --output /app/output --work /app/work
```

生成报告、CSV、SVG 并校验 result.json。预留最后 60 秒给 finalize 与校验；时间不足时先保证 result.json 合法，再补 artifacts。

## 5. 核心方法约定

- **边方向**：统一 `source` cites `target`。后向 = 种子为 source；前向 = 种子为 target。外部数据只给被引列表不给方向时标 `relation: direction_unknown`，不得猜测。
- **身份合并优先级**：DOI → arXiv → 标题+第一作者姓+年份 → 标题；保留合并证据（成员、键类型、键值）。
- **停止判据**：扩展层数上限默认 3；按相关性/年份/文献类型剪枝；无新节点即停。剪枝原因写入 `results.pruned`。
- **证据**：每个关键结论/边/数值给 `locator`（如 `materials/x.pdf#p3`）+ 逐字 `quote`；冲突两者都记并标 `source_conflict`；缺失标 `not_reported`。

## 6. 自检清单

逐条勾选，对应评分维度：

- [ ] 实际效果：种子身份已在材料中核实并写 `seed_key`；
- [ ] 实际效果：每条边方向正确、有来源证据，无猜测方向；
- [ ] 实际效果：预印本/会议版/期刊版/增补版已合并，合并证据可复核；
- [ ] 实际效果：关键被引出处、主要后续改进、代表性平行工作均已覆盖或注明遗漏；
- [ ] 使用增益：去重与方向规范化由脚本完成，减少人工核验与重复翻页；
- [ ] 使用增益：同输入两次运行产物一致（脚本确定性）；
- [ ] 创新增益：跨源身份对齐、方向感知扩展、路径解释至少落地一项；
- [ ] 可靠体验：空输入/坏 JSON/坏编码/超大文件不崩溃，退出码 0；
- [ ] 可靠体验：种子无引文记录、同名论文歧义、跨库字段冲突、撤稿与更正、限流或权限不足均已识别并处置；
- [ ] 生态贡献：文档齐备、边表字段与去重规则可复用。

自检命令：

```bash
python3 scripts/selftest.py
python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work
python3 scripts/main.py finalize --output /app/output --work /app/work
python3 -c "import json; r=json.load(open('/app/output/result.json',encoding='utf-8')); assert r.get('case_id') and r.get('status'); print('ok', r['status'])"
```

## 7. 降级策略

- 缺数据/缺引文记录：状态降为 `insufficient_evidence` 或 `partial`，写 `pending_human`，不补造。
- 缺软件：脚本只用 Python 标准库；若 SVG 渲染/预览工具不可用，SVG 仍产出，PDF/PNG 缺省时在 `limitations` 标 `preview_missing`。
- 接口限流/权限不足：记录到 `errors`/`degradations`，已获部分照常交付并标注边界。
- 任何中途异常：保留已落盘的 partial result.json，记录 `errors`，进程不崩溃。

## 8. 脚本接口（相对路径，在 Skill 根目录运行）

- `scripts/main.py bootstrap|dedupe|finalize [--input DIR --output DIR --work DIR]`：骨架落盘 / 身份合并与边重映射 / 校验与产物生成；缺省指向 `/input`、`/app/output`、`/app/work`。
- `scripts/citation_lib.py`：可导入工具库（去重、排序、最短路径、SVG、CSV、稳健读文件）。
- `scripts/selftest.py`：离线自检（空输入、坏 JSON、缺参数、超大文件、确定性、finalize）。

详细规则见 `references/RULES.md`；最小调用示例与示意输出见 `references/EXAMPLE.md`。

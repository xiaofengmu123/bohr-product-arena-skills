---
name: citation-tracing-v2
description: 引文追踪与关联文献发现（IR-first 架构）。以种子论文为起点，沿后向参考文献与前向施引文献做多层级扩展，用共被引、文献耦合、作者/数据集关联发现平行工作，完成去重与身份合并，输出可复核的文献集、有向引用边、证据路径、聚类与诚实状态。采用单一事实源 trace.json + 派生 result.json + 逐次校验的流水线，保证同输入两次运行结果一致、任何时刻都有合法 partial 产物。当任务要求“顺藤摸瓜”式文献检索、引文网络构建、路径解释或论文身份对齐时使用。
version: 1.1.0
---

# 引文追踪与关联文献发现

## 1. 角色与何时使用

本 Skill 把“从种子论文追踪文献”沉淀为可复用的流程与确定性工具。适用：前向/后向引文追踪、关联文献发现、引用网络结构解读、最短/最可信路径解释、去重与身份合并。不适用：普通关键词检索、全文阅读与引文语境内容分析、未经授权的数据抓取。

判断是否做对只看三件事：①扩展出的每篇文献能顺着引用边说清来路；②每条边有可回指的数据源依据（locator + 逐字 quote）；③去重合并后的清单干净到可直接进综述。

## 2. 输入契约（先发现，再假设）

运行时输入固定为：

- `/input/case.json`：案例元数据，可能含 `case_id`、`output_contract`、种子论文信息与材料清单；
- `/input/task.md`：任务说明；
- `/input/` 下可能另有 PDF、JSON、CSV、TXT 材料（Crossref/EuropePMC/publisher 响应、参考文献列表等）。

不假设固定文件名或键名：先扫描目录，再按内容识别。文本按 `utf-8-sig` → `utf-8` → `gb18030` → `latin-1` 顺序容错读取；单文件 >2MB 跳过并记入 `degradations`。实际输入文件和原论文是唯一事实依据，不依据题面领域标签补造数据。`output_contract` 的全部字段必须原样回填键名（缺值填 `null`/`[]`），不改名、不删未知键。

## 3. 输出契约

工作目录 `/app/work`，最终产物写 `/app/output/`。必须产出：

- `/app/output/result.json`（评分主产物，始终由 IR 物化而来）；
- 辅助可复核产物：`report.md`、`papers.csv`、`edges.csv`、`citation_graph.svg`（由 `main.py finalize` 生成）。

`result.json` 结构（固定键序）：

- `case_id`；
- 题目 `output_contract` 的全部字段（运行时从 `/input/case.json` 读取并原样回填）；
- `status`：`ok | partial | insufficient_evidence | input_error`；
- `seed`、`seed_key`、`research_question`；
- `results.papers`（每条含稳定 `id`、元数据、`versions` 与来源）、`results.edges`（统一 `source` cites `target` 有向边）、`results.clusters`、`results.paths`、`results.pruned`；
- `sources`（每条带 `locator` 与逐字 `quote`）、`uncertainty`（冲突/缺失/覆盖缺口）、`change_log`（每次修改的 time+content+reason）；
- `pending_human`、`errors`、`degradations`、`limitations`、`runtime`。

输出原则：先写合法 partial 骨架再分析，任何异常都不留空文件；状态诚实，缺材料就 `insufficient_evidence`，不编造文献、数值、引用或分数。

## 4. 执行流程与时间预算

总预算 900 秒、64 次模型请求、每响应 ≤16384 token。**前 3 分钟内必须先落盘一版 result.json 骨架**，此后只做增量改进；result.json 由 IR 物化，永不手写。

流水线命令（相对路径，均在 SKILL 包根目录执行）：

```bash
python3 scripts/main.py discover --input /input --output /app/output --work /app/work
python3 scripts/main.py merge    --work /app/work --patch /app/work/patch-01.json --reason "核实种子身份"
python3 scripts/main.py dedupe   --work /app/work --apply
python3 scripts/main.py rank     --work /app/work --apply
python3 scripts/main.py path     --work /app/work --from <src_id> --to <dst_id> --apply
python3 scripts/main.py stage    --work /app/work --output /app/output
python3 scripts/main.py finalize --work /app/work --output /app/output
python3 scripts/selftest.py
```

Step 0（0–3 min，≤4 次请求）——discover 建骨架：扫描 `/input`，容错读取 `case.json`，回填 `output_contract`，写 `/app/work/trace.json`（IR）与 `/app/output/result.json` 骨架。此步写盘失败也必须退出码 0 并留最小合法 JSON。

Step 1（3–9 min，约 30 次请求）——核实种子 + 一层扩展：在材料中确认种子身份（DOI/arXiv/标题），写 `seed_key`；后向抽参考文献（种子 cites 出处），前向抽施引文献（谁 cites 种子）。把结果打包成补丁文件 `patch-*.json`，用 `merge` 增量并入 IR；每条边写 `source/target/relation=cites/source_label/target_label/evidence`，证据逐字留档后 `stage` 校验落盘。

Step 2（9–13 min，约 20 次请求）——多层扩展 + 去重 + 平行发现：做第 2–3 层并剪枝；调 `main.py dedupe --apply` 完成身份合并并重映射引用边（先用 dry-run 预览合并）；用共被引/文献耦合/作者机构/数据集任务关联找不直接引用的平行工作并说明依据；识别奠基/桥接/热点节点。每次增量均 `merge` + `stage`。

Step 3（13–15 min，约 8 次请求）——排序 + 路径 + 自检 + finalize：调 `main.py rank --apply` 计算距离/相关性得分并排序，用 `main.py path --apply` 为关键远端文献写逐跳路径；逐条核对评分维度清单；最后 `main.py finalize` 生成 report/CSV/SVG 并全量校验。预留最后 60 秒给 finalize 与校验；时间不足时先保证 result.json 合法，再补 artifacts。

## 5. 核心方法约定

- 边方向统一：`source` cites `target`。后向 = 种子为 source；前向 = 种子为 target。外部数据只给被引列表不给方向时标 `direction_unknown`，不得猜测。
- 身份合并优先级：DOI → arXiv id → title+第一作者姓+年份 → title；合并证据（成员、键类型、键值）写入各 paper 的 `versions` 与 `clusters`。
- 停止判据：扩展层数上限默认 3；按相关性/年份/文献类型剪枝；无新节点即停。剪枝原因写入 `results.pruned`。
- 证据：每个关键结论/边/数值给 `locator`（如 `materials/x.json#record/0` 或 `materials/x.pdf#p3`）+ 逐字 `quote`；冲突信息两者都记并标 `source_conflict`；缺失标 `not_reported`。
- 确定性：固定键序、固定排序键、无随机种子；同输入两次运行产物一致。

## 6. 自检清单（对应评分维度）

- [ ] 实际效果：种子身份已在材料中核实并写 `seed_key`；
- [ ] 实际效果：每条边方向正确、有来源证据，无猜测方向；
- [ ] 实际效果：同文多版本（预印本/会议/期刊/增补版）已合并，合并证据可复核；
- [ ] 实际效果：关键被引出处、主要后续改进、代表性平行工作均已覆盖或注明遗漏；
- [ ] 使用增益：去重与方向规范化由脚本完成，减少人工核验与重复翻页；
- [ ] 使用增益：同输入两次运行产物一致（脚本确定性）；
- [ ] 创新增益：跨源身份对齐、方向感知扩展、路径解释至少落地一项；
- [ ] 可靠体验：空输入/坏 JSON/坏编码不崩溃，退出码 0；种子无引文记录、同名歧义、跨库冲突、撤稿更正、限流权限不足均已识别处置；
- [ ] 生态贡献：文档齐备、边表字段与去重规则可复用。

自检命令：

```bash
python3 scripts/main.py discover --input /input --output /app/output --work /app/work
python3 scripts/main.py finalize --work /app/work --output /app/output
python3 -c "import json; r=json.load(open('/app/output/result.json',encoding='utf-8')); assert r.get('case_id') and r.get('status'); print('ok', r['status'])"
python3 scripts/selftest.py
```

## 7. 降级策略

- 缺数据/缺引文记录：状态降为 `insufficient_evidence` 或 `partial`，写 `pending_human`/`limitations`，不补造。
- 缺软件：脚本只用 Python 标准库；若渲染工具不可用，SVG 仍产出纯文本，PDF/PNG 预览缺省时在 `limitations` 标 `preview_missing`。
- 接口限流/权限不足：记录到 `errors`/`degradations`，已获部分照常交付并标注边界。
- 坏 JSON/坏编码：读取失败记录到 `errors`，继续处理其余材料。
- 任何中途异常：保留已落盘的 partial result.json，记录 `errors`，进程不崩溃。

## 8. 架构（IR-first，与常见 bootstrap/finalize 方案不同）

单一事实源 `trace.json` 保存全部状态（seed、papers、edges、clusters、paths、sources、change_log）；`result.json` 仅由 `materialize()` 从 IR 派生，永不手写，杜绝状态漂移。所有增量修改走 `merge`（JSON Merge Patch 语义）自动追加 `change_log`，并在每次写入前/后经 `validate_ir` 校验门控。`discover/stage/finalize` 为同一入口的三个子命令，替代分散的多个脚本。详见 `references/rules-and-checklist.md`。
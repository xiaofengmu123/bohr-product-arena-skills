---
name: citation-tracing
description: 引文追踪与关联文献发现。从种子论文出发，沿后向参考文献与前向施引文献做多层级扩展，用共被引、文献耦合、作者或数据集关联发现平行工作，完成身份合并与去重，输出可复核的文献集、有向引用边、证据路径与诚实状态。当任务要求“顺藤摸瓜”式文献检索、引文网络构建、路径解释或论文身份对齐时使用。不负责普通关键词检索、全文阅读、引文语境分析或未授权抓取。
version: 1.0.0
---

# 引文追踪与关联文献发现

## 1. 角色与何时使用

本 Skill 把“从种子论文追踪文献”沉淀为可复用、可复核、确定性的流程与工具。适用：前向/后向引文追踪、关联文献发现、引用网络结构解读、路径解释、去重与身份合并。不适用：普通关键词检索、全文阅读理解、引文语境内容分析、未经授权的数据抓取。

判对只看三件事：扩展文献能否顺着引用边说清来路；每条边是否有可回指的数据源依据；去重合并后的清单是否干净到可直接进综述。

## 2. 输入契约（先发现，再假设）

运行时输入固定为：

- `/input/case.json`：案例元数据，可能含 `case_id`、`output_contract`、种子论文信息与材料清单；
- `/input/task.md`：任务说明；
- `/input/` 下可能另有 PDF、JSON、CSV、TXT 材料。

不假设固定文件名或键名：先扫描目录，再按内容识别。文本按 `utf-8-sig` 优先容错读取，坏编码降级；单文件超过 2MB 跳过并记入 `degradations`。`output_contract` 的全部键必须原样回填（缺值给 `null`/`[]`），不改名、不删未知键。实际输入文件和原论文是唯一事实依据，不依据题面领域标签补造数据。

## 3. 输出契约

工作目录 `/app/work`，最终产物写 `/app/output/`。必须产出：

- `/app/output/result.json`（评分主产物）；
- 可复核辅助产物：`report.md`、`papers.csv`、`edges.csv`、`citation_graph.svg`（由 `scripts/main.py finalize` 生成）。

`result.json` 必须包含：`case_id`；题目 `output_contract` 的全部字段；`status`（`ok | partial | insufficient_evidence | input_error`）；`sources`（每条带 `locator` 与逐字 `quote`）；`uncertainty`（`conflicts`/`missing`/`coverage_gaps`）；`change_log`（每次修改的 time + action + reason）；`results.papers`（每条含稳定 `id`、元数据与来源）；`results.edges`（统一 `source` cites `target` 的有向边）；`results.clusters`；`results.paths`；另加 `seed`、`seed_key`、`research_question`、`limitations`、`pending_human`、`errors`、`degradations`、`runtime`。

输出原则：先写合法 partial 骨架再分析，任何异常都不留空文件；状态诚实，缺材料就 `insufficient_evidence`，不编造文献、数值、引用或分数。

## 4. 执行流程与时间预算（阶段预算 + 早落盘）

总预算 900 秒、64 次模型请求。**前 3 分钟（180 秒）内必须先落盘 result.json 骨架**，此后只对同一文件增量修改，每次修改都 append `change_log`。四阶段硬性划分如下：

| 阶段 | 时间窗口 | 请求上限 | 必须完成的落盘物 |
|---|---|---|---|
| P0 建骨架 | 0–180 s | ≤4 | result.json partial 骨架 + state.json（`bootstrap`） |
| P1 种子+一层 | 180–540 s | ≤30 | `seed_key` + 一层前向/后向边（每条带证据） |
| P2 多层+去重+平行 | 540–780 s | ≤20 | 2–3 层扩展 + 身份合并 + 平行工作 + 节点角色 |
| P3 排序+路径+收尾 | 780–900 s | ≤8 | 排序、最短路径、`finalize` 产物 |

- Step 0（P0）`python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work`：扫描输入、回填 `output_contract`、落盘骨架与 `state.json`。此步必须写盘成功；即使写盘失败也要退出码 0 并留下最小合法 JSON。
- Step 1（P1）核实种子身份（DOI/arXiv/标题），写入 `seed_key`；后向抽参考文献（种子 cites 出处），前向抽施引文献（谁 cites 种子）。每条边写 `source/target/relation=cites/direction/source_label/target_label/evidence`，证据逐字留档。
- Step 2（P2）按需做 2–3 层扩展并剪枝；每完成一批改动即运行 `python3 scripts/main.py checkpoint --output /app/output --work /app/work --phase P2 --note "<内容>"` 落盘增量；用 `scripts/main.py dedupe` 合并身份；用共被引/文献耦合/作者机构/数据集任务关联找平行工作并说明依据；识别奠基/桥接/热点节点。
- Step 3（P3）用 `scripts/main.py rank` 排序（距离、图内流入、年份、来源置信度，不是“高被引=高相关”）；为关键远端文献用 `scripts/main.py path --output /app/output --source <id> --target <id>` 写逐跳路径；最后 `python3 scripts/main.py finalize --output /app/output --work /app/work` 生成报告/CSV/SVG 并校验。预留最后 60 秒给 finalize；时间不足时先保证 result.json 合法，再补 artifacts。

增量纪律：**任何一次模型回合结束前都必须调用 `checkpoint` 或 `save` 把已确认内容落盘**，使 result.json 始终是“当前最好状态”，进程中断也只损失未落盘的一小段。

## 5. 核心方法约定

- 边方向：统一 `source` cites `target`。后向 = 种子为 source；前向 = 种子为 target。外部数据只给被引列表不给方向时标 `direction_unknown`，不得猜测。
- 身份合并优先级：DOI → arXiv id → title + 第一作者姓 + 年份 → title；保留合并证据（`merged_from`、`merge_key_type`、`merge_key_value`）。同名不同文（同题异 DOI）不自动合并，写入 `uncertainty.conflicts` 待人工确认。
- 停止判据：扩展层数上限默认 3；按相关性/年份/文献类型剪枝；无新节点即停。剪枝原因写入 `results.pruned`。
- 证据：每个关键结论/边/数值给 `locator`（如 `materials/x.pdf#p3`）+ 逐字 `quote`；冲突信息两者都记并标 `source_conflict`；缺失标 `not_reported`。

## 6. 自检清单（提交/交付前逐条勾选）

- [ ] 实际效果：种子身份已在材料中核实并写入 `seed_key`；
- [ ] 实际效果：每条边方向正确、有来源证据，无猜测方向；
- [ ] 实际效果：同文不同版本（预印本/会议版/期刊版/增补版）已合并，合并证据可复核；
- [ ] 实际效果：关键被引出处、主要后续改进、代表性平行工作均已覆盖或注明遗漏；
- [ ] 使用增益：去重与方向规范化由脚本完成，减少人工核验与重复翻页；
- [ ] 使用增益：同输入两次运行产物一致（脚本确定性，可设 `CITATION_FIXED_TIME` 验证）；
- [ ] 创新增益：跨源身份对齐、方向感知扩展、路径解释至少落地一项；
- [ ] 可靠体验：空输入/坏 JSON/坏编码不崩溃，退出码 0；
- [ ] 可靠体验：种子无引文记录、同名论文歧义、跨库字段冲突、撤稿与更正、限流或权限不足均已识别并处置；
- [ ] 生态贡献：文档齐备、边表字段与去重规则可复用（见 `references/`）。

自检命令（等价于 `scripts/selftest.py`）：

```bash
python3 scripts/selftest.py
python3 scripts/main.py help
python3 -c "import json; r=json.load(open('/app/output/result.json',encoding='utf-8')); assert r.get('case_id') and r.get('status'); print('ok', r['status'])"
```

## 7. 降级策略（如实标注，不编造）

- 缺数据/缺引文记录：状态降为 `insufficient_evidence` 或 `partial`，写 `pending_human`，不补造。
- 缺软件：脚本只用 Python 标准库；SVG 始终产出（手写 SVG，不依赖渲染器）；若需 PDF/PNG 预览而环境缺渲染器，在 `limitations` 标注 `preview_missing`。
- 接口限流/权限不足/撤稿或更正：记录到 `errors`/`degradations`，已获部分照常交付并标注边界。
- 任何中途异常：保留已落盘的 partial result.json，记录 `errors`，进程不崩溃、退出码 0。

## 8. 脚本调用速查（相对路径，均 Python 标准库）

```text
python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work
python3 scripts/main.py checkpoint --output /app/output --work /app/work --phase P1 --note "seed verified"
python3 scripts/main.py dedupe --output /app/output
python3 scripts/main.py rank --output /app/output
python3 scripts/main.py path --output /app/output --source P0 --target P7
python3 scripts/main.py finalize --output /app/output --work /app/work
python3 scripts/selftest.py
```

字段规范与去重/方向/证据细则见 `references/field-spec.md`，评分维度核对表见 `references/checklist.md`。

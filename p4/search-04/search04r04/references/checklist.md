# 交付检查清单与降级决策表

本文件把评分维度映射为可勾选的交付检查项，并给出异常场景的判定路径。评审/复核人员与 Agent 均可按此逐条核对。

## 1. 评分维度 → 检查项

| 评分维度 | 检查项 | 通过标准 |
|---|---|---|
| 实际效果·任务完成度 | 以种子为起点完成一层以上扩展 | `results.papers` 含种子 + 至少一条 forward/backward 边 |
| 实际效果·结果正确性 | 文献真实存在、方向正确、版本合并正确 | 每条边有证据 quote；`merge_log` 可复核 |
| 实际效果·结果完整性 | 覆盖关键被引出处/后续改进/平行工作或注明遗漏 | 遗漏写在 `pending_human` 或 `uncertainty` |
| 实际效果·可核验性 | 种子标识、层级、边、数据源可独立复核 | `seed_key`+`evidence.locator`+`sources` 齐备 |
| 使用增益·质量/效率 | 脚本去重/方向规范化，减少人工核验与翻页 | `dedupe`/`rank` 由脚本完成，证据自动并集 |
| 使用增益·可验证性 | 同输入两次运行产物一致 | `finalize` 为 result.json 的纯函数，selftest 断言字节一致 |
| 创新增益·新增能力 | 跨源身份对齐、方向感知、可解释路径至少一项 | `merged_from`、`direction_unknown`、`paths` 至少一项存在 |
| 创新增益·方法改进 | 去重键、路径排序、停止判据有实质改进 | 按 references/methods.md 规则执行 |
| 可靠体验·调用易用 | 单命令完成种子→文献集 | `bootstrap`→`record`→`finalize` 可复现 |
| 可靠体验·运行稳定 | 空/坏输入不崩溃、重复运行稳定 | selftest 覆盖空输入/坏 JSON/坏编码 |
| 可靠体验·异常处理 | 无引文/同名歧义/冲突/撤稿/限流均识别处置 | 落入 `uncertainty`/`limitations`/`pending_human` |
| 生态贡献·文档/复用 | 说明、字段、规则齐全，脚本标准库 | SKILL.md + references + 脚本 |

## 2. 交付前自检命令

```bash
python3 scripts/main.py selftest
python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work
python3 scripts/main.py finalize --output /app/output --work /app/work
python3 -c "import json; r=json.load(open('/app/output/result.json',encoding='utf-8')); assert r.get('case_id') and r.get('status'); print('ok', r['status'])"
```

验收要点：selftest 输出 `N passed, 0 failed`；`result.json` 合法且含 `case_id`；`report.md`/`papers.csv`/`edges.csv`/`citation_graph.svg` 均存在且可解析。

## 3. 降级决策表

| 异常场景 | status | 必须写 | 禁止 |
|---|---|---|---|
| 空输入目录 / 无 case.json 无 task.md | `input_error` | `errors`、`status_detail` | 编造种子 |
| case.json 坏 JSON | `input_error` | `errors`（含 "bad JSON"） | 崩溃、留空文件 |
| 坏编码 | 可继续，降级读取 | `degradations`（编码名） | 跳过其余材料 |
| 种子无引文记录 | `insufficient_evidence` | `uncertainty: coverage_gap`、`pending_human` | 编造前向引用 |
| 同名论文歧义 | `partial` | `uncertainty: source_conflict` 或 `pending_human` | 直接合并 |
| 跨库字段冲突 | `partial` | 冲突双方 + `source_conflict` | 只保留一个 |
| 撤稿/更正 | `partial` | `uncertainty: retraction_correction` | 当作正常文献 |
| 接口限流/权限不足 | `partial` | `errors`/`degradations`、已获部分照常交付 | 冒充完整 |
| 软件缺渲染预览 | `partial` | `limitations: preview_missing` | 声称有 PDF/PNG |

任何中途异常：保留已落盘 partial result.json，追加 `errors`，退出码 0。

## 4. result.json 最小合法校验

- 顶层含 `case_id`、`status`（四值之一）、`sources`、`uncertainty`、`change_log`、`results`、`runtime`；
- `output_contract` 全部键在顶层存在（缺值 `null`/`[]`）；
- `results.papers` 每条含 `id`；`results.edges` 每条含 `source`、`target`；
- 全程 UTF-8，`sort_keys=True` 稳定输出。

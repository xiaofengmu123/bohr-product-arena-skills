# 交付前自检与运行清单

本文件是提交/交付前的逐项核查清单，对应评分维度：实际效果、使用增益、创新增益、可靠体验、生态贡献。逐条勾选后再提交。

## A. 结构契约（输出契约）

- [ ] `result.json` 存在且为合法 JSON（`json.loads` 可解析）。
- [ ] `case_id` 已从 `case.json` 的 `case_id`/`id`/`challenge_id` 解析，缺省 `"unknown"`。
- [ ] `output_contract` 的全部原始字段原样回填（键名、数量一致，未知键保留，缺值 `null`/`[]`/`{}`）。
- [ ] 标准字段齐全：`status / artifacts / sources / uncertainty / change_log / errors / degradations / limitations / pending_human / runtime`。
- [ ] `status` 取值合法：`ok | partial | insufficient_evidence | input_error`。
- [ ] `artifacts` 引用路径相对 `/app/output/` 且文件真实存在。

## B. 结果完整性（六类要素）

- [ ] 方法步骤：每步 输入→操作→输出→判断条件，逐条编号，无多步压一句。
- [ ] 实验设置：train/val/test 分开，硬件/软件版本/环境/种子/重复次数齐全，无条件串台。
- [ ] 参数与来源：参数表含 名称/取值/单位/作用对象/来源；正文数值与表一致。
- [ ] 数据集与划分：名称/版本/规模/预处理/划分比例/随机方式/子集用途齐全。
- [ ] 对照实验：基线名称+版本、是否同预算、评价指标、统计方式，可比结论。
- [ ] 复现与限制：复现清单（版本/环境/命令/数据获取）；限制指向具体失效场景。
- [ ] 无空节、无占位空话。

## C. 结果正确性（写得准）

- [ ] 每个数值/参数/版本与材料逐字一致，无补数、无改单位、无改有效数字。
- [ ] mean±sd、n、p 值等统计量逐字复用，限定词强度不升不降。
- [ ] train/val/test 条件、种子、命令逐条核对，无串台。

## D. 结果可核验性（说得住，本版强化）

- [ ] `evidence_map.json` 采用三层结构：`claims / facts / evidence`。
- [ ] `claim_to_fact` 与 `fact_to_claim` 双向索引齐，`coverage` 标 `full/partial`。
- [ ] 每个 fact 的 `evidence` 数组非空，每项 `locator` + `quote` 非空。
- [ ] `kind` 正确区分 `verbatim / numeric / derived / inference`；`inference` 不冒充事实。
- [ ] `match_method`、`confidence` 如实标注。
- [ ] `preserved_facts.json` 收录数值/单位/统计量逐条 quote。

## E. 缺口与冲突（不编造）

- [ ] 缺口显式标注：`MATERIAL GAP` / `pending_human` / `not_reported`。
- [ ] 冲突并列记录并标 `source_conflict`，两出处都保留。
- [ ] 无虚构引用、DOI、数值。

## F. 健壮性（可靠体验）

- [ ] 空输入目录（无 case.json/task.md）：退出码 0，写合法 `result.json`（`input_error`）。
- [ ] 有 case.json/task.md 但无科研材料：退出码 0，写合法 `result.json`（`insufficient_evidence`）。
- [ ] 坏 JSON / 坏编码：记录 `errors`/`degradations`，继续处理其余材料。
- [ ] 缺 `--input/--output/--work`：缺省 `/input`、`/app/output`、`/app/work`。
- [ ] 单文件 2MB 上限，超限跳过并记录。
- [ ] 重复运行同一输入结果一致（固定排序、无随机性）。
- [ ] 运行 `python3 scripts/selftest.py` 退出码 0。

## G. 流程与预算

- [ ] 前 3 分钟内已运行 `skeleton` 落盘 partial 结果骨架。
- [ ] 之后仅增量改进，每轮追加一条 `change_log`，不改写历史。
- [ ] 总预算 900 秒 / 64 次模型请求 / 单响应 ≤16384 token 内完成。
- [ ] `change_log` 完整记录 骨架→初稿→改进→收口。

## H. 生态贡献

- [ ] `SKILL.md` 写清用途、适用条件、调用方式、输入输出、异常处理。
- [ ] 脚本纯标准库、相对路径调用、可独立运行。
- [ ] `references/` 提供证据规范与参数表模板，可复用。
- [ ] 最小调用示例标明"示意，非正式案例结果"。

# 自检清单（对应评分维度）

收尾前逐条勾选；未勾选项禁止提交。每项标注对应评分维度：实际效果 / 使用增益 / 创新增益 / 可靠体验 / 生态贡献。

## A. 契约与产物完整性（实际效果·结果完整性 5 分）

- [ ] `/app/output/result.json` 存在且为合法 JSON（`python3 scripts/main.py validate` 打印 `VALID`）。
- [ ] `case_id` 非空；`status` 属于 `ok | partial | insufficient_evidence | input_error`。
- [ ] 若 `case.json` 含 `output_contract`，其**全部字段（含嵌套）已镜像进 result.json**，缺失值为 `null`/`[]`/`{}`，未改名、未删未知键；`contract_coverage.total_fields == fields 长度`，`missing == 0` 或已说明原因。
- [ ] 若 `case.json` 无契约，使用默认 schema，且 `contract_coverage.contract_source == "none"` 已显式记录。
- [ ] `sources`、`uncertainty`、`pending_human`、`change_log`、`errors`、`degradations`、`limitations` 均为数组。
- [ ] `change_log` 逐条含 `seq/at/action/detail`，`seq` 连续、`at` 单调不递减；所有结构变更都经 `record_change` 记录（无旁路写结构）。

## B. 检索正确性与可核验性（实际效果·正确性 15 + 可核验 5）

- [ ] 每条目标专利含 `record_id` 与公开号/申请号/优先权号的 `raw + normalized`，且 `normalize` 输出与官方登记一致（国别、种类代码、号码类别）。
- [ ] 公开号 / 申请号 / 授权号三者分开标注 `number_type`，未互相混用；有歧义时写 `issues` 并降 `confidence`。
- [ ] 权利要求为**逐字引用**（`quote`），不越界概括；无逐字文本时不写"保护了 X"。
- [ ] 同族归并标 `observed` 且 `exhaustive:false`，`family_type`（simple/observed/observed_singleton）与依据一致。
- [ ] 法律状态含 `status/effective_date/source/as_of/staleness_risk/confidence`；官方状态未知写 `unknown`，不推断"有效"。
- [ ] 每条关键结论有 `evidence[]` 条目：`locator` + 逐字 `quote`；冲突并列并标 `source_conflict`。
- [ ] `sources[]` 记录实际执行的检索/核验：`engine/query/url/retrieved_at/count/note`；无命中也记录。

## C. 数据源与授权边界（实际效果·任务完成度 15 分）

- [ ] `authorization[]` 逐源标注 `source/license_basis/tier(绿黄红)/displayable_fields/retention`。
- [ ] 未访问受限库原文、未尝试登录绕过付费墙；受限库标 `degradations` 与降级方案。
- [ ] 未把转载/聚合站当作官方登记依据；官方来源逐条标注库名、版本、查询日期。

## D. 预算与降级（可靠体验·运行稳定性 4 + 异常处理 3）

- [ ] 前 3 分钟内已落盘一版合法 `partial` 结果（`scaffold` 完成即满足）；之后只做增量改进。
- [ ] 总预算内（900s / 64 次请求 / 单响应 ≤16384 tokens）收尾；逼近超时立即 `finalize` 并标 `partial`。
- [ ] 空输入/无材料 → `insufficient_evidence`，`pending_human` 列缺口，退出码 0。
- [ ] 坏 JSON/坏编码 → 记 `errors/degradations`，继续处理其余材料。
- [ ] 检索无命中/限流/鉴权失败 → 如实写 `sources` 与 `limitations`，不补造文献、号码、状态。
- [ ] 缺软件/接口不可用 → `input_error` 或 `partial` 并说明缺失项，不伪造运行记录。

## E. 可复用与文档（生态贡献·文档 3 + 复用 4）

- [ ] `scripts/selftest.py` 全绿；空目录、坏 JSON、缺输入、正常契约四种场景各跑一次。
- [ ] `references/normalization-and-family.md` 的字段口径与本包实现一致。
- [ ] 最小调用示例在 SKILL.md 中且标明为示例，未冒充正式案例答案。
- [ ] 重复运行同一输入，`normalize/family/mirror/validate` 输出一致（确定性排序）。

## F. 禁止事项（红线）

- [ ] 未编造文献、DOI、号码、法律状态、评测分数或运行结果。
- [ ] 未提交预制答案/正式测试产物/白板。
- [ ] 未写 `GITHUB_URL.txt` 虚假链接（无真实公开链接就留空不写）。

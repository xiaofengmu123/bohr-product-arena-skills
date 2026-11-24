# 交付前自检清单（对应评分维度）

收尾前逐条勾选；任一项不满足则先补齐再 finalize。可执行自检：`python3 scripts/selftest.py`。

## 结构完整（实际效果 · 结果完整性 / 可靠体验 · 运行稳定性）

- [ ] `SKILL.md` frontmatter 含 `name` / `description` / `version`。
- [ ] `result.json` 含 `case_id`、`status`，且 `status ∈ {ok, partial, insufficient_evidence, input_error}`。
- [ ] `case.json` 的 `output_contract` 全部字段原样回填：不改名、不删未知键，缺值给 `null`/`[]`。
- [ ] 主产物 `patent_records / families / legal_status / literature / authorization / evidence / sources` 均存在。
- [ ] `runtime` 含 `started_at` / `finished_at` / `elapsed_seconds`。
- [ ] `report.md` 已生成且与 `result.json` 一致。

## 正确性（实际效果 · 结果正确性）

- [ ] 公开号/申请号/授权号/优先权号分开记录，且经 `normalize` 输出 `number_type` 与 `kind_code`。
- [ ] 号码存在歧义时保留多解释（`ambiguous=true`），采纳依据写 `uncertainty`，不静默择一。
- [ ] 同族只按共用申请号/优先权号归并；`family_type=observed`、`exhaustive=false`、`family_basis` 已写。
- [ ] 仅主题相似的候选合并已否决并记入 `merge_rejected`。
- [ ] 法律状态：未查官方登记簿时为 `unknown`，不推断"有效"；已查则含 `source/as_of/staleness_risk`。

## 可核验（实际效果 · 结果可核验性）

- [ ] 每条 `evidence` 有 `locator` + 逐字 `quote` + `strength`。
- [ ] 每条 `sources` 有 `engine` + `query` + 查询时间，可按记录复跑。
- [ ] 冲突来源并列记录并标 `source_conflict`，不静默择一。

## 不确定性显式化（本包重点 / 创新增益）

- [ ] 每条不确定项落入 `uncertainty`（`item/type/reason/impact/mitigation`）。
- [ ] `limitations` 与 `degradations` 分列：前者是能力边界，后者是环境/权限降级。
- [ ] `degradation_tier` 反映实际最坏授权层（green/yellow/red）。
- [ ] `unverified` 证据未冒充事实，已同步进 `uncertainty` 或 `pending_human`。

## 异常与诚实（可靠体验 · 异常处理）

- [ ] 空输入 → `insufficient_evidence`，退出码 0。
- [ ] 坏 JSON / 坏编码 → 记入 `errors`/`degradations`，继续处理其余材料，不崩溃。
- [ ] 检索无命中 / 限流 / 鉴权失败 → 如实写 `sources` + `limitations`，标 `partial`，不补造。
- [ ] 字段缺失 / OCR 边界不明 → 写 `not_reported` 或 `uncertainty`，不硬猜。

## 预算护栏（可靠体验 · 调用与易用性）

- [ ] 前 3 分钟内已落盘一版合法 `result.json`，之后只做增量改进。
- [ ] 总预算 900 秒 / 64 次模型请求 / 单响应 ≤16384 tokens，未超。
- [ ] `python3 scripts/main.py --help` 退出码 0；`validate` 打印 `VALID`。

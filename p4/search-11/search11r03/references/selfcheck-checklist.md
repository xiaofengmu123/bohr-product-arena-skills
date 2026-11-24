# 自检清单（提交前逐条勾选）

逐条对应评分维度。收尾前必须全部勾选；脚本 `scripts/selftest.py` 自动验证带 [脚本] 标记的项。

## 结构（对应"可靠体验/生态贡献"）

- [ ] [脚本] `python3 scripts/main.py --help` 退出码 0
- [ ] [脚本] `python3 scripts/main.py scaffold` 在 3 分钟内可落盘合法 partial result.json
- [ ] [脚本] `python3 scripts/main.py finalize` 校验契约、补 runtime、生成 report.md
- [ ] [脚本] `python3 scripts/main.py validate` 打印 `VALID`
- [ ] [脚本] 空目录 / 坏 JSON / 缺输入三种异常退出码 0 且产物合法
- [ ] [脚本] 同一输入两次 scaffold，`canonical` 输出逐字节一致（确定性）

## 输出契约（对应"结果可核验性/完整性"）

- [ ] result.json 含非空 `case_id` 与合法 `status`（ok|partial|insufficient_evidence|input_error）
- [ ] output_contract 的全部键原样回填（缺值给 null/[]，不改名、不删未知键）
- [ ] 含 `sources` / `uncertainty` / `pending_human` / `change_log` / `errors` / `degradations` / `limitations`
- [ ] 每条关键结论都有 `evidence` 项（locator + 逐字 quote）
- [ ] 冲突结论并列记录并标 `source_conflict`，不静默择一

## 专利号与同族（对应"结果正确性"）

- [ ] 每个号码经 `normalize`：国家码、号码、kind、number_type、confidence 齐备
- [ ] 公开号/申请号/优先权号/授权号不混用（number_type 逐条判定）
- [ ] 同族经 `family` 归并：成员、family_type、basis、`exhaustive:false` 齐备
- [ ] 未观测到的同族链接不臆造；只标 observed/extended 并注明"非穷尽"
- [ ] 权利要求只做逐字引用，不越界概括

## 法律状态（对应"结果正确性"）

- [ ] 状态来源、生效日、as_of、staleness_risk、confidence 逐条记录
- [ ] 官方当前状态未知时写 `unknown`，不推断"有效"
- [ ] 不把转载页面当登记依据；官方库与二手来源并列标 `source_conflict`

## 技术文献（对应"任务完成度"）

- [ ] 每条文献含 rank/title/authors/year/venue/doi/source/relevance_rationale/evidence
- [ ] 区分专利文献与技术文献的检索逻辑，检索式可解释（sources 记录 query）
- [ ] 无命中/限流/鉴权失败如实写入 sources 与 limitations，不补造文献

## 授权边界（对应"任务完成度"）

- [ ] 每个数据源标 license_basis、tier（绿/黄/红）、displayable_fields、retention
- [ ] 受限库/付费墙标 degradations 并给降级方案，不尝试登录或绕过

## 诚实性（对应"状态说明"）

- [ ] status 诚实：材料不足=insufficient_evidence，运行失败=input_error
- [ ] 缺字段写 not_reported，不硬猜；OCR 边界不明写 ocr_uncertain
- [ ] 不编造文献、号码、状态、分数或运行记录

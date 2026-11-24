# 规则与自检清单

本文把号码规范化、同族归并、法律状态核验、授权边界与降级策略的具体规则
写清楚，供执行 Agent 与复核人对照；收尾前逐条勾选。

## 1. 号码规范化规则

- **来源优先**：从官方登记页（USPTO/Espacenet/PATENTSCOPE/国家局）取回的号码
  以官方字段为准；从扫描件/OCR 文本抽取的号码先经 `extract` 再逐条核对，
  不得依据文件名或标签脑补内容。
- **类型判别**：
  - `publication`：公开/授权号，常见形态 `US 10,123,456 B2`、`WO 2020/123456 A1`、
    `CN 110123456 A`、`EP 3 123 456 A1`。
  - `application`：申请号，常见形态 `US 16/123,456`、`PCT/US2020/012345`、
    `EP 12 345 678.9`、`CN 201910123456.7`（13 位）。
  - `priority`：优先权号，US 临时申请 `62/123,456` 或 `61/123,456` 系列。
- **不硬猜**：纯数字无法区分公开/申请时，`number_type` 标 `publication` 且
  `confidence=medium`，`note` 写明"需回官方登记核实"；无法识别标 `unknown`+`low`。
- **OCR 粘连处理**：WO/EP 扫描件中相邻字符可能合并（如 `WO2020/123456A1` 被拆
  或粘连），`extract` 后对候选做上下文核对；边界不明写 `uncertainty` 的
  `ocr_uncertain`，不强行拆分。

## 2. 同族归并规则

- **口径三档**：
  - `simple`：共用同一优先权的成员（严格同族）。
  - `extended`：通过中间优先权关系间接关联（扩展族）。
  - `observed`：仅基于给定材料中观察到的共用申请号/优先权号归并（本 Skill 默认）。
- **默认标注**：自动归并结果一律 `family_type="observed"` 且 `exhaustive=false`，
  明确"不是穷尽法律族谱"；只有能核验官方同族关系时才升为 simple/extended。
- **归并键优先级**：priority > application > publication；无法归并的记录单独成族
  并标 `single`，不把仅关键词命中的几件不同专利当同一族。
- **口径不明**：写 `uncertainty` 的 `family_scope_unclear`，不静默择一。

## 3. 法律状态核验规则

- **官方优先**：状态以官方登记库为准，转载页面/聚合站只能作为旁证，不得当登记依据。
- **必填字段**：`status`、`effective_date`、`source`、`as_of`（查询日期）、
  `staleness_risk`（滞后风险）、`confidence`。
- **状态未知**：官方状态无法确认就写 `unknown`，绝不推断"有效"；写 `pending_human`。
- **冲突**：两来源状态不一致时，双方证据并列并标 `source_conflict`，同时写
  `staleness_risk` 与 `pending_human`，不静默择一。

## 4. 授权边界规则

- **绿/黄/红分级**：
  - 绿：公开可访问、可展示、可留存（官方公开页、公开专利全文）。
  - 黄：可访问但展示/留存有限制（需注明许可条款、留存期限）。
  - 红：受限库/付费墙/企业保密（不尝试登录、不绕过、不上传、不展示原文）。
- 每条数据源落 `authorization[*]`：`{source, license_basis, tier, displayable_fields,
  retention}`；受限库标 `degradations` 说明不可用与降级方案。

## 5. 自检清单（交付前逐条勾选）

- [ ] `SKILL.md` frontmatter 含 name / description / version。
- [ ] `scripts/main.py --help` 退出码 0，六子命令齐全。
- [ ] `scripts/selftest.py` 全部 PASS（空目录 / 坏 JSON / 缺输入 / 契约回填 /
      规范化 / 同族归并 / finalize / validate）。
- [ ] `result.json` 含 `case_id`、`status`、`output_contract` 全部字段、
      `sources`、`uncertainty`、`change_log`。
- [ ] 每条关键结论有 `locator` + 逐字 `quote`；冲突标 `source_conflict`。
- [ ] 同族结果标 `observed` 且 `exhaustive:false`，口径与归并依据写明。
- [ ] 法律状态有 `source`+`as_of`+`staleness_risk`，未知写 `unknown` 不推断。
- [ ] 数据源有授权分级 `tier`，受限库标 `degradations` 不绕过。
- [ ] `status` 诚实：材料不足 `insufficient_evidence`，运行失败 `input_error`。
- [ ] 未编造文献、号码、状态、分数或运行记录。
- [ ] 文件数 5–8、总体积 60–140KB（超出可接受但需说明）。

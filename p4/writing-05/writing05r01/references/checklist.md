# 提交/交付前自检清单（checklist）

逐条核对；未满足者写入 `limitations` 并如实说明，不得编造。

## A. 实际效果（40 分）

- [ ] 三段交付齐备：`claim_list`（论断清单）、`evidence_map`（证据表）、`deliverable.draft`（成稿）。
- [ ] 每一条**实质性**论断（含数字、术语、比较、因果、建议）都进入 `evidence_map`，不是只挂表面引用。
- [ ] 每条论断的 support 都含 `source_doc` + `locator` + 逐字 `quote`，且 quote 与原文逐字一致（已跑 `validate` 确认）。
- [ ] 引用真实性已核：著录信息、编号与参考文献表一致；二手转引、撤稿、版本差异已在 `note`/`uncertainty` 标注。
- [ ] 四类要素齐备：论断、来源、证据强度标注、不确定性表述。
- [ ] 论断编号、原文摘录、定位信息、强度理由能被他人独立回查（locator 可查 `locator_index.json`）。

## B. 使用增益（25 分）

- [ ] 相比"通用改写"，本结果给出可逐条回溯的证据映射，减少回原文核对与补引用返工。
- [ ] 每条建议是具体编辑动作（改写为…/加限定语/删除/换来源），不是通用写作原则。
- [ ] `change_log` 记录 scaffold/analyze/diagnose/finalize 的版本与边界，可支撑增益对照。

## C. 创新增益（15 分）

- [ ] 产出含"论断—来源关联 + 证据强度 + 逐字 quote 回查"的结构级产物。
- [ ] `evidence.py`（locator 索引 + 逐字回查）、`references/evidence_rubric.md`（分级口径）可独立复用。
- [ ] `scripts/selftest.py` 覆盖正常/空目录/坏 JSON/quote 不一致四类场景。

## D. 可靠体验（10 分）

- [ ] 缺材料、仅摘要、原文与摘要矛盾、证据冲突、格式不支持、PDF 无文本层等均识别并写入 `limitations`/`uncertainty`。
- [ ] 重复运行不覆盖已改进结果（scaffold 默认不覆盖非 `in_progress` 版本）。
- [ ] `result.json` 为合法 JSON，含 `case_id`、`status`、全部 `output_contract` 键、`sources`、`uncertainty`、`pending_human`、`change_log`、`limitations`。

## E. 生态贡献（10 分）

- [ ] `SKILL.md` frontmatter 齐备（name/description/version）；正文写清用途、输入输出契约、执行流程、自检、降级。
- [ ] `scripts/`、`references/` 齐备；依赖仅 Python 标准库 + 可选 `pdftotext`；路径、限制、最小示例标注为示例。
- [ ] 沉淀可分享的失败类型与核验方法（quote 不一致、来源不可回查、强度不足、冲突证据）。

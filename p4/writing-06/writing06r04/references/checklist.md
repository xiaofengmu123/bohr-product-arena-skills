# 结构诊断可复用检查清单

提交/交付前逐条勾选；每条对应评分维度。清单可脱离本 Skill 独立用于论文结构自查。

## A. 诊得准（定位到章节/段落/句子）

- [ ] 每个 issue 有 location（章节、段落、句子引文），而非泛泛的写作原则。
- [ ] 每个 issue 标注六维之一与具体 problem_type。
- [ ] 每条问题有逐字 quote 支撑，无虚构缺陷、无把风格偏好当结构错误。

## B. 改得动（可执行编辑动作）

- [ ] 每条 issue 有 edit_action.action_type（move/merge/split/narrow/add_transition/delete/rewrite/add_limitation/reorder/restate）。
- [ ] 每条动作含 target_location 与可执行 instruction，而非"另起一篇改写稿"。
- [ ] 收窄类（narrow）给出限定到何种数据/条件的明确边界。

## C. 链得通（闭环与论据）

- [ ] 研究问题在引言、方法、结果、结论四处定位并检查是否一致回扣。
- [ ] claim_evidence_map 覆盖正文主要论断，每条有 strength 判定（sufficient/insufficient/missing/circular/leap）。
- [ ] 跨章节呼应（方法—结果—讨论）逐一核对，不一致处列为 issue。

## D. 说得清（可复核）

- [ ] 每条判断可指回 locator + 逐字 quote。
- [ ] source_map 与 sources 每个元素都含 claim_id、locator、quote、confidence。
- [ ] 作者复核后能对每条 issue 判定 accept/partial/reject。

## E. 六维覆盖

- [ ] 研究问题与贡献主张对齐：已检查并记录结论。
- [ ] 章节结构与叙事顺序：已检查并记录结论。
- [ ] 论证链与论据对应：已检查并记录结论。
- [ ] 段落关系：已检查并记录结论。
- [ ] 重复内容：已检查并记录结论。
- [ ] 结论边界与表述尺度：已检查并记录结论。
- [ ] 未发现问题的维度在 dimension_coverage 标"未见结构问题"并给依据，而非留空。

## F. 异常与降级

- [ ] 缺章节/无参考文献/图表公式缺失/格式混乱/稿件截断：均在 limitations 标注，未编造。
- [ ] 图表读数不清、单位不明、统计量无法确定：写入 pending_human，正文用"[待确认：xxx]"。
- [ ] 材料不足的维度标"证据不足"，status 如实为 partial。
- [ ] 运行失败保留已落盘 partial 结果，change_log 说明原因。

## G. 交付与可复用

- [ ] SKILL.md frontmatter 含 name/description/version。
- [ ] 脚本仅 Python 标准库，相对路径可执行（先 cd /skills/weekly-skill）。
- [ ] 说明、依赖、配置、范围、限制齐全。
- [ ] 本清单与六维分类法可被他人复用。

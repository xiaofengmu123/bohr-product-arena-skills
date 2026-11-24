# 自检清单与评分锚点

## 一、执行前必查
- [ ] 已读 `/input/case.json` 与 `task.md` 原文，未按文件名臆测材料内容。
- [ ] 已读材料原文（`materials/manuscript-baseline.md`、`materials/reviewer-comments.md` 等），PDF 用解析/渲染工具。
- [ ] 前 3 分钟内已落盘 `status=partial` 的 result.json 骨架与三份台账骨架（`scripts/main.py scaffold` + `ledger`）。
- [ ] 每次修改追加 `change_log`，改完重跑 `scripts/main.py validate`。

## 二、实际效果（40 分）——交付质量锚点
- [ ] 每条意见及其全部子要求都有明确处理结论，无笼统致谢、无“只答一半”。
- [ ] 每条 accept/partial 有 `revision_map` 条目，`before/after_quote` 与 `locator` 可在稿件中独立核对。
- [ ] 回复信每条“已修改”引用 `CHG-x`，且该条目 `status=done`；事实/数字/引用逐条映射到 `source_ref`。
- [ ] 需补实验/数据/文献/分析的每条意见有 `evidence` 条目；`not_possible` 有 `fallback`。
- [ ] 回复信—修改稿—稿件版本三者一致；多轮返修上一轮承诺记入 `version_boundary.changes` 且仍成立。

## 三、使用增益（25 分）——与基础 Agent 的对照方式
- [ ] 台账先行 + 骨架先落盘，减少逐条比对、找行号、重写语气、补证据清单的时间。
- [ ] 提供可复跑对照：同一输入下“不加载 Skill（直接写回复信）”与“加载 Skill（先建台账再生成）”结果差异，
      以 `validate` 输出的问题数为客观证据，而非仅文字自述。

## 四、创新增益（15 分）——方法边界与价值
- [ ] 明确本方法的实质改进：以三份结构化台账作为中间表示，把“已修改/已补证据”从自由文本变成可校验的
      引用关系（`CHG-x`/`E-x`），基础 Agent 难以稳定做到。
- [ ] 说明边界：不覆盖完整审稿系统、不联网核验评审全文、不补做真实实验；只解决“答得全/改得准/证得住/对得上”。

## 五、可靠体验（10 分）——异常与稳定性
- [ ] 空输入目录、坏 JSON、缺材料、版本不匹配、权限不足：分别处理，`status` 诚实，脚本退出码 0 不崩溃。
- [ ] 长稿件、多意见、多轮返修、重复运行：要素不漂移，目标与版本编号前后一致。
- [ ] 意见互相冲突/意见本身有误/无法补实验/稿件缺章节：识别并写入 `pending_human`、`anomalies` 或 `fallback`。

## 六、生态贡献（10 分）——可复用与可分享
- [ ] 使用说明、输入模板、依赖、示例、边界、维护信息齐全，说明与提交版本一致。
- [ ] 意见分类法（type/severity/tone）、回复信模板、定位脚手架、台账 schema、自检脚本可被他人直接复用。
- [ ] 沉淀可分享的返修案例与分类经验；最小调用示例标明为示例，不冒充正式结果。

## 七、最终交付前
- [ ] `python scripts/selftest.py` 通过（空目录/坏 JSON/缺输入/正常路径/一致性校验五场景）。
- [ ] `python scripts/main.py validate --out /app/output` 输出 `"problems": []`。
- [ ] result.json 含 `case_id` + output_contract 三字段 + `status/sources/uncertainty/pending_human/change_log`。

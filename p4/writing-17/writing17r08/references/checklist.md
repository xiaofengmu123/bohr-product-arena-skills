# 自检与评分维度对照（references/checklist.md）

交付前逐条勾选；每条对应题目评分维度，是「实际效果 / 可靠体验 / 使用增益」的落地
检查项。运行 `python3 scripts/selfcheck.py --output /app/output` 得到机器检查结果，
下面为需要 Agent 自行核对的内容。

## 实际效果（抓得准 / 理得清 / 连得对 / 证据可追溯）

- [ ] 已读 case.json 与 task.md，明确导图用途、目标读者、材料范围、交付格式。
- [ ] 根节点=研究主题/任务目标，不是材料标题的机械复制。
- [ ] 节点是核心概念/问题/方法/证据/结论/任务/风险，剔除了装饰性词。
- [ ] 层级 ≤ 6 层，同层粒度一致，无分类交叉、无重复节点。
- [ ] 每个关系都有方向、类型和材料依据；无凭空连接。
- [ ] 冲突信息用 `conflict` 关系并保留两条来源，未隐藏。
- [ ] 关键节点与关系都带 locator+quote；provenance 已区分 fact/summary/inference。
- [ ] 数字、术语、引用均可逐条回溯，未新增材料外事实。

## 可靠体验（调用与易用性 / 稳定性 / 异常处理）

- [ ] 第一步已运行 `python3 scripts/main.py --input /input --output /app/output --work /app/work`，
      3 分钟内 result.json 骨架落盘。
- [ ] 每次改进后运行 `python3 scripts/mindmap.py --draft /app/work/draft.json --output /app/output`。
- [ ] 交付前运行 `python3 scripts/selfcheck.py --output /app/output`，FAIL 项已清零或已解释。
- [ ] 空输入、坏 JSON、坏编码、缺 pdftotext 均已按降级规则处理，未崩溃。
- [ ] 重复运行脚本结果一致（确定性，无随机种子问题）。

## 使用增益（可验证性）

- [ ] result.json 的 change_log 记录了骨架→抽取→渲染→修订各步骤与时间。
- [ ] runtime.seconds 与 degradation 记录真实，未虚构运行时长或分数。
- [ ] 主产物可编辑（.md/.json/.mm）且可预览（.svg，或含 .png）。

## 输出契约（result.json）

- [ ] case_id 与 /input/case.json 一致。
- [ ] 原 output_contract 全部字段已在 result.json 顶层回填（缺值用 null，不改名、不删键）。
- [ ] status ∈ {ok, partial, insufficient_evidence, input_error} 且与材料充分度一致。
- [ ] sources / uncertainty / change_log 均为数组，内容诚实。
- [ ] limitations / pending_human 覆盖所有未确认项与缺口。

## 提交前终检

- [ ] 总文件数 ≤ 14，总体积 ≤ 1.2MB，脚本只用标准库。
- [ ] 未携带示例答案、未预写正式测试产物、未复制题面大段文本。
- [ ] SKILL.md 调用路径为相对路径 scripts/xxx.py，可执行。

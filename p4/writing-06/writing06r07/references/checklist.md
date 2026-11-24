# 自检清单与验收（references/checklist.md）

提交/交付前逐条勾选。每条对应评分维度；脚本 `scripts/verify.py`（经
`python scripts/main.py verify` 调用）与 `scripts/selftest.py` 只做机械校验，
本清单覆盖"人工复核"部分。脚本校验通过**不等于**结构诊断正确，仍需逐条人工核对。

## A. 实际效果（40 分）

- [ ] 任务完成度（15）：已对完整稿件产出结构诊断 + 作者可直接执行的修改方案；
      体裁、受众、格式符合 task.md，产物为可编辑 Markdown（`diagnosis.md`）。
- [ ] 结果正确性（15）：每个 finding 与稿件实际内容相符——未虚构不存在的缺陷、
      未漏掉真实断裂、未把风格偏好当结构错误；逐条复核 quote 与原文一致。
- [ ] 结果完整性（5）：六类（研究问题、章节结构、论证链、段落关系、重复内容、
      结论边界）均已检查；未被检查的类别已说明"非本稿主要问题"或"材料缺失"。
- [ ] 结果可核验性（5）：每个 finding 有 locator + 逐字 quote；quote 可在原稿定位；
      `source_map` 逐条可回溯，无新增材料外事实。

## B. 使用增益（25 分）

- [ ] 质量增益（15）：诊断定位到句级，编辑动作具体（移动/合并/拆分/收窄），
      优于只给笼统原则的基础回答。
- [ ] 效率增益（5）：作者从诊断到定稿的轮次下降——每条建议给了 disposition，
      作者可一次采纳/驳回，无需追问。
- [ ] 增益可验证性（5）：同一稿件、同一口径下，本结果可复现（重跑脚本 +
      重读规则得到一致诊断路径），change_log 记录返工次数。

## C. 创新增益（15 分）

- [ ] 新增能力（5）：已产出论点—论据映射表（`source_map`），标出
      sufficient/insufficient/gap/loop/leap。
- [ ] 方法改进（5）：六维问题分类 + 动词化编辑动作，区别于语言润色工具；
      每个 finding 带 dim/problem_type/action 三要素。
- [ ] 创新证据（5）：能说明本方法相对"润色工具"的边界——只做结构判断，
      不替作者重写正文。

## D. 可靠体验（10 分）

- [ ] 调用与易用性（3）：SKILL.md 触发条件明确；`python scripts/main.py skeleton`
      → `evidence` → `verify` 三命令可独立运行，缺省路径指向 /input、/app/output。
- [ ] 运行稳定性（4）：空输入、坏 JSON、缺参数、超大文件均优雅降级；
      重复运行结果一致（随机种子无关，布局确定）。
- [ ] 异常处理（3）：缺章节、无参考文献、图表公式缺失、格式混乱、稿件截断均已
      识别并写入 `uncertainty` / `version_boundary.limitations`，未编造。

## E. 生态贡献（10 分）

- [ ] 文档完整性（3）：name/description/version、依赖（仅标准库）、路径、范围、
      限制、失败处理齐全。
- [ ] 资源可复用性（4）：六维规则、问题分类、映射表模板、检查清单、脚本可被
      他人直接复用。
- [ ] 分享推广价值（3）：问题分类与改稿动作表可沉淀为标注模板，供同类任务复用。

## 机械校验（脚本可做）

- [ ] `python scripts/selftest.py` 全部 PASS（空输入/坏 JSON/超大文件/编码/确定性）。
- [ ] `python scripts/main.py verify` 无 FAIL；WARN 逐条说明原因或已修复。
- [ ] `result.json` 为合法 UTF-8 JSON，含 case_id、status、output_contract 全部字段、
      sources、uncertainty、pending_human、change_log。
- [ ] status 非 `in_progress`（交付前必须置终值 complete/partial/insufficient_evidence）。

## 结果字段速查

| 字段 | 含义 | 何时非空 |
|---|---|---|
| `case_id` | 案例标识（取自 case.json 的 case_id/id） | 恒有 |
| `status` | complete/partial/insufficient_evidence/input_error | 恒有（终值非 in_progress） |
| `diagnosis` | 六维结构诊断 + 可执行编辑方案（主产物） | 诊断完成 |
| `source_map` | 论点—论据映射（link_verdict） | 论证链检查完成 |
| `version_boundary` | 修改差异、人工确认项、材料缺口 | 恒有（缺材料时写 limitations） |
| `artifacts` | 可编辑文件路径 + 预览路径 | 落盘文件时 |
| `sources` | finding 级 locator + quote + evidence | 有 finding 时 |
| `uncertainty` | 材料不足、冲突、未确认项 | 有不确定项时 |
| `pending_human` | 需作者裁定的项 | 有需人工确认项时 |
| `change_log` | 时间顺序的骨架/增量修改记录 | 恒有 |

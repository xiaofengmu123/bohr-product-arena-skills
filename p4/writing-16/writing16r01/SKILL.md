---
name: research-writing-transfer
description: 科研成果转化写作 Skill：按给定源文、目标读者、体裁与篇幅完成一次可直接使用的转化改写（如论文摘要改科普文章、技术报告改项目申报材料），保留事实、数据、单位、图表与引用的来源定位，输出可编辑产物与 result.json。当任务要求依据科研材料做受众适配、内容取舍、术语一致或文体转换时使用。
version: 1.0.0
---

# 科研成果转化写作 Skill

## 1. 角色与何时使用

本 Skill 指导 Agent 把科研材料（论文、技术报告、研究摘要、审稿意见等）改写成**目标读者可读、可直接使用**的转化稿：科普文章、项目申报材料、简报、政策摘要、双语摘要等。适用信号：输入含"源文/事实底稿 + 目标读者 + 体裁 + 篇幅约束"；或案例要求"生成、改写、校对、引用格式、双语、结构化写作"并要求保留来源。不适用：一次性脚本、纯聊天问答、无源材料的凭空写作。

Agent 的角色是**严谨的转化编辑**：只做受众适配与内容取舍，不新增源材料之外的事实、数字或结论；每处删改都能按段复核、可回改。

## 2. 输入契约（运行时）

- 原始输入目录 `/input/`：必有 `case.json`（含 `case_id`、任务说明、`output_contract`、受众/体裁/篇幅约束、材料路径）与 `task.md`。
- 材料位置：`/input/materials/`、`/input/` 下 PDF/MD/TXT/JSON/CSV，或其他由 case.json 指定的路径；**以 case.json 为准，不假设固定文件名/键名**。
- 工作目录 `/app/work/`（中间产物），最终产物目录 `/app/output/`。
- 读取一律用 `scripts/evidence_lib.py` 的容错读取：编码按 utf-8-sig → utf-8 → gb18030 → latin-1 降级；单文件 >2MB 跳过并记 degradation。
- 脚本参数缺省：`--input /input`、`--output /app/output`、`--work /app/work`。

## 3. 输出契约

`/app/output/result.json` 必须存在且为合法 JSON，包含：

- `case_id`：来自 case.json，原样保留；
- case.json `output_contract` 声明的**全部字段**：不改名、不删未知键；缺值给 `null`/`[]` 并记入 `pending_human`；
- `status`：`ok | partial | insufficient_evidence | input_error`，诚实取值；
- `sources`：数组，每项 `{locator, quote, claim}`，数字/术语/引用/图表/关键论断逐条可回溯；
- `uncertainty`：数组，每项 `{item, level, reason}`，标注证据强度与不确定处；
- `change_log`：数组，每项 `{paragraph, change_type, reason, before, after}`，逐段删改清单；
- 另加 `artifacts`（主产物路径映射）、`errors`、`degradations`、`pending_human`、`runtime`。

主产物（改写稿）必须是**可编辑文本**，`.md` 优先，其次 `.docx`/`.json`/`.csv`，写入 `/app/output/`；若产出 Office/幻灯片/图表，需同时给真实渲染的 PDF/PNG 预览。主产物路径必须写入 result.json 的 `artifacts`。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求 / 单响应 ≤16384 tokens）

时间纪律：**前 3 分钟内必须先落盘一版 result.json 骨架**，之后只做增量改进；随时可先交 partial，绝不因追求完美而空手。

| 步骤 | 动作 | 输入 | 输出 | 预算 |
|---|---|---|---|---|
| S0 脚手架 | `python3 -B scripts/main.py --input /input --output /app/output --work /app/work` | /input | result.json 骨架 + work/fact_inventory.json | ≤60s |
| S1 读题 | 读 case.json/task.md，确认受众、体裁、篇幅、output_contract 全部键 | /input | work/plan.md | ≤60s / 3 请求 |
| S2 事实清单 | 读全部材料，核对数字/单位/样本量/统计口径/图表号/引用，锁定术语 | 材料 | work/glossary.md + 事实清单 | ≤120s / 8 请求 |
| S3 受众与取舍 | 定受众画像、必保留/可压缩/可删除清单、论断强度分级 | plan.md | work/triage.md | ≤120s / 10 请求 |
| S4 成稿 | 写完整转化稿（不是提纲/建议），逐段改写，删改记入 change_log | 材料+清单 | /app/output/<主稿>.md | ≤300s / 20 请求 |
| S5 溯源 | 为每个数字/术语/引用/关键论断填 sources（locator+quote） | 成稿 | sources 数组 | ≤120s / 10 请求 |
| S6 自检收尾 | `python3 -B scripts/validate.py`；修 WARN；填 uncertainty/pending_human；更新 status | result.json | 最终 result.json | ≤120s / 10 请求 |

S0 必须最先执行，失败也要继续（脚本内部已降级）；每步先写再改，S4 前先有可打开的空稿，避免崩溃时无产物。

## 5. 核心改写规则（四题眼）

- **读得懂**：缩写首次出现必须展开并附全称；不引入未解释的公式/行话；科普稿用类比与例子，删除只服务同行评审的元话语。
- **留得住**：关键结论、数据、限定条件、样本信息、指标口径、图表编号与引用**一个不删**；只删冗余与方法细节中读者不需要的部分，并在 change_log 说明理由。
- **说得准**：相关不写成因果、初步结果不写成定论；用 `evidence_lib.classify_strength` 检查每个论断动词；同一概念全文只用一个说法（术语表锁定）。
- **接得住**：每段给出"原文位置 → 改后文本 → 改动类型 → 理由 → 待确认项"，原作者可逐段复核、继续编辑。

## 6. 自检清单（逐条可勾选，对应评分维度）

- [ ] 实际效果·完成度：产出完整可用的转化稿，而非写作建议/提纲/片段润色（15 分）
- [ ] 实际效果·正确性：数字、单位、图表号、引用与论断强度均与源文一致，无源文外新结论（15 分）
- [ ] 实际效果·完整性：关键结论/数据/限定/方法要点/图表引用均保留，删的是冗余不是证据（5 分）
- [ ] 实际效果·可核验：提供逐段对照、改动清单、删改理由、待确认问题（5 分）
- [ ] 使用增益：同源文/同读者/同体裁/同篇幅下，事实漂移、受众错配、术语不一致更少（25 分）
- [ ] 创新增益：形成"受众画像→取舍策略→边界标注→多文体映射"链条，术语表/强度分级/逐段对照可复用（15 分）
- [ ] 可靠体验：重复运行结果一致；长文分段；文体切换可用；异常被识别并处置（10 分）
- [ ] 生态贡献：SKILL.md、体裁模板、术语表、验收口径、失败处理、版本信息齐全（10 分）
- [ ] 契约完备：result.json 含 case_id + 全部 output_contract 字段 + status/sources/uncertainty/change_log；主产物可打开
- [ ] 诚实：不编造分数、不编造实验数据；材料不足标 insufficient_evidence，冲突标 source_conflict

## 7. 降级策略

- **缺数据**：材料不足或关键字段缺失 → `status=insufficient_evidence`，`pending_human` 列出缺口；不编造数据/引用/DOI。
- **坏 JSON/坏编码**：脚本容错读取并记 errors/degradations，继续处理其余材料，不崩溃。
- **缺软件**：无 docx/pptx 库 → 用 `.md` 纯文本交付并记 degradation；无 LaTeX/渲染 → 标注"未渲染预览"，不假装成功。
- **写盘失败**：降级到最小合法 result.json（仅 case_id/status/errors），进程退出码仍为 0。
- **篇幅冲突**：体裁与篇幅上限矛盾时，先保"正确与可复核"，再压缩，冲突记入 uncertainty。
- **涉密/未发表**：不扩散原文，只引用必要片段，记入 limitations/degradations。

## 8. 脚本

- `scripts/main.py`：S0 脚手架——容错发现输入、抽取 output_contract、尽早落盘 result.json 骨架、生成 fact_inventory.json。
- `scripts/evidence_lib.py`：共享库——容错读取、文件发现、事实/数字/引用抽取、证据映射、论断强度分级。
- `scripts/validate.py`：S6 自检——校验 result.json 契约、字段完整性与主产物存在性。

最小调用示例（示意，非正式案例结果）：

```bash
python3 -B scripts/main.py --input /input --output /app/output --work /app/work
# 之后按 S1–S6 写稿并回填 result.json
python3 -B scripts/validate.py --input /input --output /app/output
```

---
name: sci-writing-transfer
description: 科研成果转化写作 Skill：读取科研事实底稿与受众、体裁、篇幅约束，产出可编辑的改写稿、逐段改动清单与可回溯证据映射，保持数字、单位、引用、图表编号和论断强度与源文一致。当需要把论文、技术报告等改写成科普文章、项目申报材料、双语或结构化稿件时使用。
version: 1.0.0
---

# 科研成果转化写作 Skill

## 1. 角色与何时使用

本 Skill 面向"科研成果转化写作"：拿到事实底稿（论文、技术报告、数据、图表）与目标读者、体裁、格式、篇幅约束后，产出**可直接使用的改写稿**，而不是提纲、建议或片段润色。四项目标贯穿全程：

- **读得懂**：缩写首次展开、术语/行话按受众解释，删冗余不删证据。
- **留得住**：关键结论、数据、样本量、统计口径、图表编号与引用完整保留。
- **说得准**：论断强度与源文证据匹配，不把相关写成因果、不把初步结果写成定论，同一概念全文同一说法。
- **接得住**：每处删改给出清单、理由与逐段对照，他人可逐条复核、原作者可继续编辑。

触发条件：输入目录存在科研材料且任务要求"改写/翻译/校对/结构化/格式转换"。**非触发**：纯问答、无材料的一次性写作、无明确受众与体裁的随笔。

## 2. 输入契约（运行时）

- 运行时输入固定为 `/input/case.json` 与 `/input/task.md`；科研材料（PDF、docx、md、txt、csv、图）按题面放在 `/input` 下。**先扫描目录，不假设固定文件名/键名**；`case.json`、`task.md`、`materials/` 都要能识别。
- 工作目录 `/app/work`，最终产物目录 `/app/output`。
- PDF 用平台 pdf-text-tool 提取真实文本，不依据文件标签补造内容；docx/pptx/xlsx 用对应工具读取。
- 文本按 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 降级不崩溃）；单文件超 2MB 跳过/截断并记入 `degradations`。

## 3. 输出契约

`/app/output/result.json` **必须存在**，含 `case_id` 与下述全部字段（缺值给 `null`/`[]`，不改名、不删未知键），外加题目要求的 `status / sources / uncertainty / change_log`：

| 字段 | 类型 | 说明 |
|-|-|-|
| `case_id` | string | 来自 case.json 的稳定标识，缺失时置 `null` 并记 `errors` |
| `status` | string | `ok`\|`partial`\|`insufficient_evidence`\|`input_error`，如实标注 |
| `deliverables` | list | `[{path,type,description}]`，主产物（md/docx/tex/svg+pdf）路径相对 `/app/output` |
| `evidence_map` | list | `[{locator,quote,claim,type,status}]`：每项带定位符+逐字引用，`status` 标 `verified`/`source_conflict`/`not_reported` |
| `sources` | list | `[{path,size_bytes,role}]`，材料清单 |
| `uncertainty` | dict | `{claim_strength_note, unresolved[]}`：论断强度分级与未决项 |
| `change_log` | list | `[{id,paragraph,action,before,after,reason}]`：逐段删改对照 |
| `pending_human` | list | 需人工确认项 |
| `material_gaps` | list | 材料缺口/源文未支持的结论 |
| `errors` | list | 运行错误 |
| `degradations` | list | 降级记录（截断、坏编码、缺工具） |
| `runtime` | dict | `{start,end,seconds,stage}` |

**主产物**必须是可编辑、可复现的格式：文本稿 `.md`（或 `.docx`/`.tex`）；图形给可编辑 SVG + 真实 PDF/PNG 预览；Office 产物给可打开文件并附渲染预览。事实/数字/引用逐条能指回 `evidence_map` 的 `locator + quote`，冲突信息两条都记并标 `source_conflict`，缺失写 `not_reported` 或 `pending_human`，**不虚构引用、DOI、数值**。

## 4. 执行流程与预算

总预算 **900 秒、64 次模型请求**；先落盘骨架、再增量改进，任何阶段中断都留合法产物。

| 步骤 | 输入 | 输出 | 预算 |
|-|-|-|-|
| 0 骨架落盘（**前 3 分钟内必须完成**） | `/input` | `python3 scripts/main.py --input /input --work /app/work --output /app/output` 生成合法 `result.json` 骨架（status=partial）与 `requirements.json` | 1 次请求 |
| 1 事实锁定 | 材料 | `python3 scripts/extract.py` 生成 `evidence_map.json`；建立术语表（缩写首展开、同义词收敛） | 2–6 次 |
| 2 受众画像与取舍 | case 约束 | 判定读者背景/目的，把每处源文分为"必须保留/可压缩/可删除"，绝不动证据 | 2–4 次 |
| 3 起草改写稿 | 取舍表 | 目标体裁/篇幅的完整改写稿（可编辑），按论断强度阶梯用词 | 6–18 次 |
| 4 逐段对照复核 | 稿+源文 | 逐段改稿 + `change_log`；校验每处数字/引用/图表编号 | 4–10 次 |
| 5 落盘终稿 | 复核 | 写 deliverables、最终 `result.json`；`python3 scripts/validate.py --output /app/output` | 2–6 次 |

**论断强度阶梯**（用词随证据，逐条标注）：证实/表明（established）→ 显示（shown）→ 初步表明（preliminary）→ 有望/可能（prospective）；相关≠因果，样本限制必须保留。

## 5. 自检清单（对应评分维度）

- [ ] 实际效果·完成度：产出的是完整可用的改写稿，非提纲/建议（15 分）
- [ ] 实际效果·正确性：数字/单位/引用/图表编号/论断强度与源文一致，无源文外新结论（15 分）
- [ ] 实际效果·完整性：关键结论、数据、限定条件、方法要点、图表引用齐全，删的是冗余（5 分）
- [ ] 实际效果·可核验：逐段对照 + 改动清单 + 删改理由 + 待确认问题齐备（5 分）
- [ ] 使用增益：术语一致、事实零漂移、篇幅满足上限，改动可被原作者直接采纳（25 分）
- [ ] 创新增益：走完"受众画像→取舍策略→边界标注→多文体映射"链条（15 分）
- [ ] 可靠体验：可重复运行、长文分段、多文体切换稳定，异常如实上报（10 分）
- [ ] 生态贡献：SKILL.md、术语表、保留项清单、体裁规则、改动清单格式、失败处理齐全（10 分）
- [ ] 运行前：`python3 scripts/main.py --help` 退出码 0；空目录、坏 JSON、缺输入三场景各跑一次均不崩溃

## 6. 降级与诚实标注

- 材料不足 → `status=insufficient_evidence`，列出缺口，**不写示例填充、不编造数据/结论**。
- 缺软件/工具（PDF 解析、Office 渲染失败）→ 记 `degradations`，改用可用文本，`status=partial` 并写明局限。
- 数据自相矛盾 → 两条都记入 `evidence_map` 标 `source_conflict`，不擅自选一个。
- 体裁与篇幅冲突、材料涉密/未发表 → 记 `pending_human` 或 `material_gaps`，改写稿保留可编辑占位并说明。
- 写盘失败 → 降级到最小合法产物，进程不崩溃；重复运行同一输入结果一致（无随机性）。

## 7. 脚本与资源

- `scripts/main.py`：骨架落盘 + 需求抽取（`--input/--work/--output` 缺省指向 `/input`、`/app/work`、`/app/output`）
- `scripts/extract.py`：数字/引用/图表/术语证据台账
- `scripts/validate.py`：result.json 契约校验
- `references/genre_rules.md`：体裁/受众/术语与论断强度规则
- `references/checklist.md`：改稿自检与验收口径

脚本仅用 Python 标准库；在 Skill 根目录用相对路径调用，如 `python3 scripts/main.py --input /input --work /app/work --output /app/output`。

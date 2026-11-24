---
name: academic-talk-organizer-v2
description: 将论文或研究材料组织为证据可追溯、可编辑、符合模板的学术汇报；用 typed 中间表示 plan.json 把“内容决策”与“契约合规”解耦，逐页唯一主张+证据强度+图表引用+讲稿备注+问答预演，并做渲染检查与自检。用于把固定科研案例做成可上台、可复核的演示文件并保留页级证据映射。
version: 1.1.0
---

# 学术汇报内容组织 Skill（IR 版）

## 1. 角色与何时使用

本 Skill 让 Agent 在“从已有研究材料到可讲页面”这一环节稳定产出可复核结果。任一条件满足即触发：

- 任务要求把论文 / 研究材料组织成演示文稿，且需逐页标注主张、图表与证据来源；
- 输入含听众、时长、页数、模板等约束，需要内容取舍与叙事顺序设计；
- 评分要求证据可追溯、导出可打开、结论强度与证据匹配。

不适用于：论文精读总结、学术写作成稿、科研绘图素材生产（相邻课题负责）。

**核心方法（与逐节搬运式方案的本质区别）**：先定“可检查的报告目标”和“听众质疑”，再在“问题—方法—结果—结论”主骨架上做取舍；每页只保留一条唯一主张 + 支撑证据 + 证据强度，配套讲稿与问答准备；最后逐页回填来源定位并检查渲染导出。内容决策全部写入 typed 中间表示 `plan.json`，契约合规与证据校验由脚本确定性完成，二者解耦。

## 2. 输入契约

运行时原始输入（只读，勿改）：

- `/input/case.json`：案例标识、任务约束、材料清单；可能含 `output_contract`，若存在则**原样保留其全部字段**（键名不改、未知键不删、缺值填 `null`/`[]`）。
- `/input/task.md`：任务说明。
- 材料文件（PDF / 图片 / 模板 / 起始 deck）按 `case.json` 清单定位，通常在 `/input/` 或 `/app/work/`。
- 可用工具：`pdf-text-tool`、`pdf-render-tool`、`ocr-tool`、`latex-compiler`、`office-renderer`、`browser-automation`、`font-discovery`、`scientific-python`。

工作目录 `/app/work/`；最终产物写入 `/app/output/`。**实际输入与原论文是事实依据，不得依据领域标签补造数据。**

## 3. 输出契约

必须产出 `/app/output/result.json`：含 `case_id` 与 `output_contract` 全部字段，并追加：

- `status`：`complete` / `partial` / `insufficient_evidence` / `input_error` / `failed`（如实标注）。
- `sources`：实际读取/发现的来源（`path`、`type`、`read`）。
- `uncertainty`：无法判断、材料缺失、证据不足等逐条说明。
- `pending_human`：需人工补材料或决策的待办项。
- `change_log`：`timestamp + action + scope` 增量记录。
- `runtime`：生成器、版本、起止时间、耗时、issue 列表。

若 `case.json` 未给出 `output_contract`，使用默认契约：`report_goal`、`audience`、`narrative`、`pages`、`editable_deck`、`evidence_map`、`qa_prep`、`rendering`。字段定义见 `references/output_contract.md`。

同时把可编辑演示文件（PPTX / HTML / ODP）与渲染预览（PDF / PNG）写入 `/app/output/`，并在 `rendering` 记录源文件、导出件、字体、模板、修改记录。未产出的字段留空或显式标 `null`，不得编造。

## 4. 中间表示 plan.json（本版差异化核心）

Agent 的所有内容决策写入 `/app/work/plan.json`；`scripts/main.py` 只做校验与组装，不代写科学内容。这样“每页主张是否对”“取舍是否合理”由 Agent 负责，“字段是否齐全”“证据是否可定位”由脚本保证，重复运行结果确定。

`plan.json` 顶层：`case_id`、`report_goal`、`audience`、`narrative`、`pages`、`evidence_map`、`qa_prep`、`editable_deck`、`rendering`、`sources`、`uncertainty`、`pending_human`、`change_log`。`pages[i]` 含 `page_index`、`title`、`single_claim`（唯一主张）、`evidence_strength`（high/medium/low）、`figures[]`、`speaker_notes`、`source_refs[]`。**每个 `source_refs` 项必须同时含 `locator`（定位）与 `quote`（逐字原文），缺一记为 error。** 完整 schema 见 `references/output_contract.md`。

## 5. 执行流程（总预算 900 秒、64 次模型请求）

每步输出先落盘、后改进。**前 180 秒内必须先落盘 `result.json` 骨架**，此后只做增量改进。请求数为上限，不追求用完；到 850 秒即收尾。

| 步骤 | 输入 | 输出 | 时间预算 | 请求预算 |
|-|-|-|-|-|
| 1 读取与落盘骨架 | case.json、task.md、材料清单 | 跑 `python3 scripts/main.py` 生成 result.json 骨架 | ≤60s | 3–5 |
| 2 证据抽取 | 材料全文 | plan.json 的 `source_refs`（locator + 逐字 quote） | 60–180s | 10–15 |
| 3 主线与页规划 | 证据 + 听众约束 | plan.json 的 report_goal/audience/narrative/pages/qa_prep | 180–420s | 15–25 |
| 4 生成演示件 | pages + 模板 | 可编辑 deck + speaker_notes + 导出 PDF/PNG | 420–660s | 10–20 |
| 5 组装与证据校验 | plan + deck | 跑 `python3 scripts/main.py` 组装并出 validation_report | 660–780s | 3–5 |
| 6 渲染与自检 | result + deck | 跑 `python3 scripts/selftest.py` | 780–870s | 3–5 |
| 7 增量收尾 | 自检报告 | 修复缺陷、更新 change_log | 870–900s | 余量 |

增量更新方式：每次修改 `plan.json` 后重新运行 `python3 scripts/main.py`，它会重校验、重组装，`change_log` 自动保留骨架时间戳并追加新条目，无需手改 result.json。

## 6. 自检清单（逐条可勾选，对应评分维度）

- [ ] 实际效果：report_goal、audience、narrative、pages、qa_prep 齐全且形成闭环。
- [ ] 每页恰好一条 `single_claim`，无摘要/Results 逐段搬页。
- [ ] 每页数字、图表引用与源材料一致；`evidence_strength` 与证据匹配。
- [ ] 六方面齐全：论文主线、页结构、图表引用、讲解逻辑、模板约束、导出渲染。
- [ ] 证据可核验：每个 `source_refs` 有 locator + 逐字 quote；`main.py` 无 error 级 issue。
- [ ] 使用增益：取舍决策写入 narrative/change_log，可复核；不空谈效率。
- [ ] 创新增益：听众适配、页级规划、问答预演均有落地字段；IR 解耦真实生效。
- [ ] 可靠体验：可编辑文件 + PDF/PNG 预览存在；`selftest.py` 全 PASS。
- [ ] 异常处理：缺图/低分辨率/公式丢失/字体缺失/导出失败/超时均如实标注。
- [ ] 生态贡献：脚本仅标准库、相对路径调用；references 说明完整、他人可复用。

完整映射见 `references/checklist.md`。

## 7. 降级策略

- 材料缺失或无法定位来源：保留 `single_claim`，`evidence_strength` 标 `low`，记入 `uncertainty`，**不得补造出处**。
- 图表缺失/低分辨率：页面注明“图缺失 / 不可用”，记入 `rendering`，不生成伪造图。
- 公式/符号丢失：在 `uncertainty` 标注丢失位置，不改写公式；能用 LaTeX 重排则注明“重排自原文”。
- 字体/模板/office-renderer 不可用：退回纯文本大纲或 HTML deck，`status` 标 `partial`，说明降级原因。
- 导出失败或超时：保留可编辑源文件与部分结果，`status` 如实标 `partial`/`failed`，记入 change_log。
- 缺失软件状态或科学数据一律只标 limitations，禁止编造。

## 8. 脚本调用（相对路径，仅标准库）

```bash
# 步骤 1：立即落盘 partial 骨架（前 180 秒内必须完成）
python3 scripts/main.py --input /input --work /app/work --output /app/output

# 内容规划完成后重新运行：校验 plan.json → 组装 → 定 status
python3 scripts/main.py --input /input --work /app/work --output /app/output

# 仅强制重写骨架（忽略已有 plan.json）
python3 scripts/main.py --skeleton-only --input /input --work /app/work --output /app/output

# 提交前自检（空输入、坏 JSON、IR 往返三种场景）
python3 scripts/selftest.py
```

脚本路径相对 `SKILL.md` 所在目录；`--help` 可查参数。引擎在 `scripts/core_impl.py`，入口 `scripts/main.py` 保持精简。字段定义见 `references/output_contract.md`，评分对应清单见 `references/checklist.md`。

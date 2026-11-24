---
name: academic-slide-organizer
description: 把论文或研究材料组织为证据可追溯、可编辑、符合模板的学术汇报（逐页唯一主张、图表引用、讲解顺序、讲稿备注、问答准备、渲染检查），并以内容寻址 run_id、canonical 序列化与 scripts/selftest.py 自检保证同一输入两次产物逐字节一致。用于把固定科研案例做成可上台、可复核、可复现的演示文件并保留页级证据映射。
version: 2.0.0
---

# 学术汇报内容组织 Skill（确定性版）

## 1. 角色与何时使用

本 Skill 负责"从已有研究材料到可讲页面"的组织与交付，任一条件满足即触发：

- 任务要求把论文/研究材料组织成演示文稿，且需逐页标注主张、图表与证据来源；
- 输入含听众、时长、页数、模板等约束，需要内容取舍与叙事顺序设计；
- 评分要求证据可追溯、导出可打开、结论强度与证据匹配、同输入可复现。

不适用于：论文精读总结、学术写作成稿、科研绘图素材生产（相邻课题负责）。

核心方法：先定报告目标与听众，再定"问题—方法—结果—结论"主线与页级取舍；每页一条唯一主张 + 支撑证据 + 证据强度，配套讲稿与问答准备；最后逐页回填来源定位并检查渲染导出。全程以确定性脚本落盘，任何一步失败都不留空产物。

## 2. 输入契约

运行时原始输入（只读，勿改）：

- `/input/case.json`：案例标识、任务约束、材料清单；可能含 `output_contract`，若存在必须原样保留全部字段。
- `/input/task.md`：任务说明与评分维度。
- 材料文件（PDF/图片/模板/起始 deck）按 `case.json` 清单定位，通常位于 `/input/` 或 `/app/work/`。
- 可用工具：`pdf-text-tool`、`pdf-render-tool`、`ocr-tool`、`latex-compiler`、`office-renderer`、`browser-automation`、`font-discovery`、`scientific-python`。

工作目录 `/app/work/`；最终产物写入 `/app/output/`。PDF 必须用 `pdf-text-tool`/`pdf-render-tool` 读取实际文本与页面，不依据文件名或领域标签补造内容。

## 3. 输出契约

必须产出 `/app/output/result.json`：含 `case_id` 与题目 `output_contract` 的全部字段（未给契约时用本 Skill 默认契约），再附信封字段 `status` / `sources` / `uncertainty` / `pending_human` / `errors` / `degradations` / `change_log` / `run`。字段定义与 locator/quote 约定见 `references/contract.md`。

同时写入 `/app/output/`：

- 可编辑演示文件（PPTX / HTML / ODP）与渲染预览（PDF / PNG）；
- 页级证据映射 `evidence_map`（每项 `locator` + 逐字 `quote`）；
- 未产出的字段留空或显式标 `null`，不得编造；缺失就写 `not_reported` 或 `pending_human`。

`status` 取值：`complete` / `partial` / `failed` / `insufficient_evidence` / `input_error`，必须诚实。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

每步输出先落盘，后改进。**前 180 秒内必须先落盘 result.json 骨架**，此后只做增量改进；到 850 秒即收尾并跑 `finalize`。

| 步骤 | 输入 | 输出 | 时间预算 |
|-|-|-|-|
| 1 读取与核对 | case.json、task.md、材料 | 材料清单、约束、output_contract | ≤60s，3–5 请求 |
| 2 落盘骨架 | 步骤 1 结论 | `main.py scaffold` 生成 partial result.json | ≤120s，1–2 请求 |
| 3 主线与规划 | 材料全文 | report_goal / audience / narrative / pages 初稿 | 120–300s，8–15 请求 |
| 4 生成演示件 | pages + 模板 | 可编辑 deck + speaker_notes | 300–540s，10–20 请求 |
| 5 证据映射 | pages + 来源定位 | evidence_map，跑 `evidence_check.py` | 540–660s，5–10 请求 |
| 6 渲染与自检 | deck + 导出件 | PDF/PNG 预览，跑 `main.py check` | 660–780s，4–8 请求 |
| 7 增量收尾 | 自检报告 | 修复缺陷、`main.py finalize`、更新 change_log | 780–900s，余量 |

- 步骤 2 之后所有内容修改都通过 `main.py update --patch <文件> --action "..."` 增量合并，禁止整体重写 result.json。
- 请求数为预算上限，不追求用完；每个关键结论在写进 pages 前先定位源材料并摘录逐字 quote。

## 5. 自检清单（对应评分维度）

逐条可勾选清单见 `references/checklist.md`，覆盖：实际效果 40 分（闭环、唯一主张、事实一致、证据强度）、使用增益 25 分（返工减少、取舍可复核、确定性）、创新增益 15 分（听众适配、页级规划、问答预演）、可靠体验 10 分（可编辑+预览、异常识别）、生态贡献 10 分（文档、标准库脚本、可复用）。收尾必查：`selftest.py` 全部 PASS。

## 6. 降级策略

- 材料缺失或无法定位来源：保留 `single_claim`，`evidence_strength` 标 `low`，`locator` 写 `not_reported`，记入 `uncertainty`，不得补造出处。
- 图表缺失/低分辨率/公式或符号丢失：页面注明"图缺失/不可用"，记入 `degradations` 与 `rendering`，不生成伪造图。
- 字体/模板/office-renderer 不可用：退回纯文本大纲或 HTML deck，`status` 标 `partial`，说明降级原因。
- 导出失败或超时：保留可编辑源文件与部分结果，`status` 标 `partial`/`failed`，记入 `change_log`。
- 坏 JSON/坏编码/空输入：`main.py scaffold` 捕获并写 `input_error`/`insufficient_evidence`，退出码 0，不崩溃。
- 缺失软件状态或科学数据一律只标注 limitations，禁止编造。

## 7. 脚本调用（相对路径，仅标准库）

```bash
python3 scripts/main.py scaffold --input /input --task /input/task.md --output /app/output
python3 scripts/main.py update --patch /app/work/patch.json --action "add pages" --scope "pages"
python3 scripts/evidence_check.py --result /app/output/result.json
python3 scripts/main.py check --output /app/output
python3 scripts/main.py finalize --output /app/output
python3 scripts/selftest.py
```

脚本只用 Python 标准库，路径相对 SKILL.md 所在目录；均可用 `--help` 查看参数。字段定义见 `references/contract.md`，检查清单见 `references/checklist.md`。

## 8. 确定性约定（本版核心）

1. `main.py` 以 canonical JSON 落盘（键排序、紧凑分隔符、禁用 NaN）；来源按路径排序。
2. `run_id` 为内容寻址标识（`case_id` + 各来源 `sha256`），固定案例重复运行保持相同。
3. `change_log` 用确定性 `step` 序号，不写墙钟时间戳，保证逐字节复现。
4. 任何布局/随机抽样固定随机种子并在 `rendering` 记录种子值。
5. 交付前运行 `scripts/selftest.py`：校验 frontmatter、标准库依赖、`--help` 退出码、骨架合法性、两次运行逐字节一致、空输入与坏 JSON 降级。

---
name: academic-deck-organizer
description: 将论文/研究材料组织为证据可追溯、可编辑、符合模板的学术汇报（页级唯一主张、图表引用、讲稿备注、问答准备、渲染检查），并以鲁棒性优先：空输入、坏JSON、缺参数、超大文件均优雅降级并如实标注 limitations，不编造数据。用于固定科研案例做成可上台、可复核的演示文件并保留页级证据映射。
version: 1.1.0
---

# 学术汇报内容组织 Skill（鲁棒性优先）

## 1. 角色与何时使用

本 Skill 让 Agent 在“从已有研究材料到可讲页面”环节稳定产出可复核结果。任一条件满足即触发：

- 任务要求把论文 / 研究材料组织成演示文稿，且需逐页标注主张、图表与证据来源；
- 输入含听众、时长、页数、模板等约束，需要内容取舍与叙事顺序设计；
- 评分要求证据可追溯、导出可打开、结论强度与证据匹配、异常可解释。

不适用于：论文精读总结、学术写作成稿、科研绘图本身的素材生产（相邻课题负责）。

核心方法：先定报告目标与听众，再定“问题—方法—结果—结论”主线与页级取舍；每页一条唯一主张 + 支撑证据 + 证据强度，配套讲稿与问答准备；最后逐页回填来源定位并检查渲染导出。

**本版设计取舍（与常见包不同）**：把“绝不因坏输入崩溃”作为第一优先级。所有读取走鲁棒路径，坏 JSON / 坏编码 / 空目录 / 超大文件都只产生 `errors` / `degradations` 记录，进程退出码仍为 0 并落盘合法 partial 结果；证据强度与结论措辞始终由源材料决定，读不到就标 low，绝不补造。

## 2. 输入契约

运行时原始输入（只读，勿改）：

- `/input/case.json`：案例标识、任务约束、材料清单；可能含 `output_contract`，若存在则**以其字段为准并保留全部字段**。
- `/input/task.md`：任务说明。
- 材料文件（PDF / 图片 / 模板 / 起始 deck）按 `case.json` 清单定位，通常位于 `/input/` 或 `/app/work/`。
- 可用工具：`pdf-text-tool`、`pdf-render-tool`、`ocr-tool`、`latex-compiler`、`office-renderer`、`browser-automation`、`font-discovery`、`scientific-python`。

工作目录 `/app/work/`；最终产物写入 `/app/output/`。SKILL 安装在 `/skills/weekly-skill/`，脚本一律用相对路径 `scripts/core_impl.py` 调用。

**鲁棒性约定**：不假设固定文件名/键名，先扫描输入目录再解析；文本按 `utf-8-sig` 优先读取并容错（BOM、坏编码、坏 JSON 均降级不崩溃）；单文本文件默认上限 2MB、JSON 上限 8MB，超限截断或跳过并记入 `degradations`。

## 3. 输出契约

必须产出 `/app/output/result.json`：含 `case_id` 与 `output_contract` 全部字段，并附 `status`、`sources`、`uncertainty`、`pending_human`、`errors`、`degradations`、`change_log`、`runtime`。若 `case.json` 未给出 `output_contract`，使用默认契约（字段定义见 `references/output_contract.md`）：

- `case_id`：案例标识（优先 `case.json.id`，其次 `armiId`）。
- `report_goal`：报告目标（可检查目标、听众、时长、页数、验收标准）。
- `audience`：听众先验、关注点、可能质疑。
- `narrative`：页面序列与取舍理由（非按论文目录搬运）。
- `pages`：页级结构数组，每页含 `page_index`、`title`、`single_claim`、`evidence_strength`（high/medium/low）、`figures`、`speaker_notes`、`source_refs`。
- `editable_deck`：可编辑演示文件（路径、格式、所用模板）。
- `evidence_map`：每页主张 → 来源定位（图/表/章节/页码/数据），每项带 `locator` + 逐字 `quote`，未改写事实。
- `qa_prep`：预判追问、口径一致回答、可引用页面。
- `rendering`：源文件、导出件（PDF/PNG 预览）、字体、模板、修改记录。
- `status`：`complete` / `partial` / `failed` / `insufficient_evidence` / `input_error`（诚实标注）。

可编辑演示文件（PPTX / HTML / ODP 等）与渲染预览（PDF / PNG）一并写入 `/app/output/`。未产出的字段留空或显式标 `null`，不得编造。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

每步输出先落盘，后改进。**前 3 分钟（180 秒）内必须先写出一版 `/app/output/result.json` partial 骨架**，此后只做增量改进。

| 步骤 | 输入 | 输出 | 时间预算 |
|-|-|-|-|
| 1 读取与核对 | case.json、task.md、材料 | 材料清单、约束、output_contract | ≤60s，2–4 请求 |
| 2 落盘骨架 | 步骤 1 的 case_id / 契约 | `scripts/main.py --scaffold-only` 生成 result.json 骨架 | ≤120s，1 请求 |
| 3 主线与规划 | 材料全文 | report_goal / audience / narrative / pages 初稿 | 120–300s，8–15 请求 |
| 4 生成演示件 | pages + 模板 | 可编辑 deck + speaker_notes | 300–540s，10–20 请求 |
| 5 证据映射 | pages + 来源定位 | evidence_map，写 `sources.json` 后跑 `--sources` 校验 | 540–660s，5–10 请求 |
| 6 渲染与自检 | deck + 导出件 | PDF/PNG 预览，跑 `--validate-only` 与 `selftest.py` | 660–780s，4–8 请求 |
| 7 增量收尾 | 自检报告 | 修复缺陷、更新 change_log | 780–900s，余量 |

- 增量改进统一用 `scripts/main.py --patch /app/work/update-*.json` 深合并进 result.json，保证骨架始终最新且不被整文件重写破坏。
- 请求数为预算上限，不追求用完；到 850 秒即收尾并写最终 result.json。

## 5. 自检清单（对应评分维度）

逐条见 `references/checklist.md`；自动检查：

```bash
python3 scripts/main.py --help                 # 退出码 0
python3 scripts/main.py --input /input --work /app/work --output /app/output --scaffold-only
python3 scripts/main.py --input /input --output /app/output --sources /app/work/sources.json --validate-only
python3 scripts/selftest.py                    # 空目录/坏JSON/缺参数/超大文件 5 项全过
```

- 实际效果：report_goal、audience、narrative、pages、qa_prep 齐全且闭环（40 分）。
- 每页恰好一条 `single_claim`；数字与图表引用和源材料一致；`evidence_strength` 与证据匹配。
- 六方面齐全：论文主线、页结构、图表引用、讲解逻辑、模板约束、导出渲染。
- 证据可核验：evidence_map 每项有 `locator` + `quote`，`--validate-only` 无 error。
- 使用增益：取舍决策写入 change_log 可复核；导出修正有记录。
- 创新增益：听众适配、页级规划、问答预演均有落地字段。
- 可靠体验：可编辑文件 + PDF/PNG 预览均存在；selftest 全过。
- 异常处理：缺图 / 低分辨率 / 公式丢失 / 字体缺失 / 导出失败 / 超时均如实标注。
- 生态贡献：脚本仅标准库、相对路径调用，references 说明完整。

## 6. 降级策略

- 材料缺失或无法定位来源：保留 `single_claim`，`evidence_strength` 标 `low`，记入 `uncertainty`，不得补造出处。
- 图表缺失或低分辨率：页面注明“图缺失 / 不可用”，记入 `rendering`，不生成伪造图。
- 字体 / 模板 / office-renderer 不可用：退回纯文本大纲或 HTML deck，`status` 标 `partial`，说明降级原因。
- 导出失败或超时：保留可编辑源文件与部分结果，`status` 如实标 `partial` / `failed`，记入 change_log。
- 空目录 / 坏 JSON / 坏编码 / 缺参数 / 超大文件：脚本退出码 0，落盘合法结果并如实标注（`selftest.py` 验证）。
- 缺失软件状态或科学数据一律只标注 limitations，禁止编造。

## 7. 脚本调用（相对路径，仅标准库）

```bash
python3 scripts/main.py --input /input --work /app/work --output /app/output --scaffold-only
python3 scripts/main.py --input /input --output /app/output --patch /app/work/update-pages.json
python3 scripts/main.py --input /input --output /app/output --sources /app/work/sources.json --validate-only
python3 scripts/selftest.py
```

脚本只依赖 Python 标准库（3.9+），路径相对 `SKILL.md` 所在目录；均可用 `--help` 查看参数。字段定义见 `references/output_contract.md`，评分对应清单见 `references/checklist.md`。

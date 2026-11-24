---
name: academic-talk-organizer
description: 将论文或研究材料组织为证据可追溯、可编辑、符合模板的学术汇报。核心是三层可复核中间表示（plan.json 叙事规划、figure_model.json 图表事实模型、result.json 交付契约），逐页唯一主张、图表引用、讲解顺序、讲稿备注、问答准备与渲染检查；用于把固定科研案例做成可上台、可复核的演示文件并保留页级证据映射。区别于把论文压缩搬页的做法，本 Skill 强调从源材料到成稿的取舍过程可被独立复核。
version: 1.1.0
---

# 学术汇报内容组织 Skill

## 1. 角色与何时使用

本 Skill 负责"从已有研究材料到可讲页面"这一环节，产出可复核结果。任一条件满足即触发：

- 任务要求把论文 / 研究材料组织成演示文稿，并逐页标注主张、图表与证据来源；
- 输入含听众、时长、页数、模板等约束，需要内容取舍与叙事顺序设计；
- 评分要求证据可追溯、导出可打开、结论强度与证据匹配。

不适用于：论文精读总结、学术写作成稿、科研绘图素材生产（相邻课题负责）。
核心方法：先定报告目标与听众，再定"问题—方法—结果—结论"主线与页级取舍；每页
一条唯一主张 + 支撑证据 + 证据强度，配套讲稿与问答准备；最后逐页回填来源定位并
检查渲染导出。学术汇报不是论文压缩：组会讲方法与进度、答辩讲创新与边界、评审讲
结论与证据强度，共同约束"哪些上页、按什么顺序、每页留哪一句"。

## 2. 核心设计：三层中间表示（本版重点）

可复核性是本题得分关键。本 Skill 用三个结构化 JSON 分层记录，最终演示文件可由
中间表示重建，任何一页都能逐层回溯：

- `plan.json`（叙事规划层）：报告目标、听众、叙事取舍、逐页唯一主张、证据强度、
  图表绑定、讲稿备注、问答准备、来源定位。
- `figure_model.json`（图表事实模型层）：每个上页图/表/公式的事实卡——种类、
  locator、逐字 quote、提取数字、可用性状态、被哪些页使用。
- `result.json`（交付契约层）：合并上述两层，加 `status` / `sources` /
  `uncertainty` / `pending_human` / `change_log`。

追溯链为：页 → plan 条目 → figure/evidence 的 locator + 逐字 quote。字段规范见
`references/output_contract.md`。取舍理由（为何某图不上页、为何调序）必须写入
`narrative.tradeoffs` 或 `change_log`，这是"可复核"的最低要求。

## 3. 输入契约

运行时原始输入（只读，勿改）：

- `/input/case.json`：案例标识、任务约束、材料清单；可能含 `output_contract`，若
  存在则以其中字段为准并**原样保留全部字段**（缺值给 `null`/`[]`，不改名、不删）。
- `/input/task.md`：任务说明。
- 材料文件（PDF / 图片 / 模板 / 起始 deck）按 `case.json` 清单定位，通常在
  `/input/` 或 `/app/work/`；PDF 解析拿到的是原始 PDF，不要依据文件标签臆测内容。
- 可用工具：`pdf-text-tool`、`pdf-render-tool`、`ocr-tool`、`latex-compiler`、
  `office-renderer`、`browser-automation`、`font-discovery`、`scientific-python`。

工作目录 `/app/work/`；最终产物写入 `/app/output/`。

## 4. 输出契约

必须产出 `/app/output/result.json`，含 `case_id`、题目 output_contract 全部字段，
再加 `status` / `sources` / `uncertainty` / `pending_human` / `change_log`。若
`case.json` 未给出 output_contract，使用默认字段：`report_goal`、`audience`、
`narrative`、`pages`、`editable_deck`、`evidence_map`、`qa_prep`、`rendering`。

- `pages`：页级结构数组，每页含 `page_index`、`title`、`single_claim`（唯一主张）、
  `evidence_strength`（high/medium/low）、`figures`、`speaker_notes`、`source_refs`。
- `evidence_map`：每页主张 → `locator` + 逐字 `quote`，未改写事实。
- `editable_deck`：可编辑演示文件（路径、格式、所用模板）。
- `rendering`：源文件、导出件（PDF/PNG 预览）、字体、模板、修改记录。
- `status`：`complete` / `partial` / `failed` / `insufficient_evidence` / `input_error`。
- 可编辑演示文件（PPTX / HTML / ODP）与渲染预览（PDF / PNG）一并写入
  `/app/output/`。未产出的字段留空或显式标 `null`，不得编造。

## 5. 执行流程（总预算 900 秒、64 次模型请求、每响应 16384 token）

**前 3 分钟内必须先落盘一版 `result.json` 骨架**（`scaffold`），之后只做增量改进。
请求数为预算上限，不追求用完；到 850 秒即收尾写最终 `result.json`。

| 步骤 | 输入 | 输出 | 时间预算 |
|-|-|-|-|
| 1 读取与核对 | case.json、task.md、材料 | 材料清单、约束、output_contract | ≤60s，3–5 请求 |
| 2 落盘骨架 | 步骤 1 的 case_id/契约 | `main.py scaffold` 生成 result.json + plan.json + figure_model.json | ≤120s，1–2 请求 |
| 3 主线与规划 | 材料全文 | 写 plan.json（report_goal/audience/narrative/pages/qa_prep） | 120–300s，8–15 请求 |
| 4 生成演示件 | plan.json + 模板 | 可编辑 deck + speaker_notes + figure_model.json | 300–540s，10–20 请求 |
| 5 证据映射 | plan + figure_model | `main.py update` 合并 evidence_map | 540–660s，5–10 请求 |
| 6 渲染与自检 | deck + 导出件 | PDF/PNG 预览，`main.py validate` | 660–780s，4–8 请求 |
| 7 增量收尾 | 自检报告 | 修复缺陷、更新 change_log | 780–900s，余量 |

- 每步用 `main.py update` 把 plan/figure_model 的新字段合并进 result.json，骨架始终最新。
- 脚本 `scaffold` 是幂等的：重复运行不丢已有内容；`change_log` 记录每次动作与影响范围。

## 6. 自检清单（逐条可勾选）

- [ ] report_goal、audience、narrative、pages、qa_prep 齐全且形成闭环（40 分核心）。
- [ ] 每页恰好一条 `single_claim`，无摘要逐段搬页。
- [ ] 每页数字、图表引用与源材料一致；`evidence_strength` 与证据匹配。
- [ ] 六方面齐全：论文主线、页结构、图表引用、讲解逻辑、模板约束、导出渲染。
- [ ] 证据可核验：evidence_map 每条含 locator + 逐字 quote，`validate --strict` 通过。
- [ ] 使用增益：取舍决策写入 narrative.tradeoffs / change_log，可复核。
- [ ] 创新增益：听众适配、页级规划、问答预演、中间表示均有落地字段（非空话）。
- [ ] 可靠体验：可编辑文件 + PDF/PNG 预览均存在，`validate --strict` 无阻断项。
- [ ] 异常处理：缺图/低分辨率/公式丢失/字体缺失/导出失败/超时均如实标注。
- [ ] 生态贡献：脚本只用标准库、相对路径调用，references 说明完整。
完整清单与脚本对应关系见 `references/checklist.md`。

## 7. 降级策略

- 材料缺失或无法定位来源：保留 `single_claim`，`evidence_strength` 标 `low`，记入
  `uncertainty`，不得补造出处。
- 图表缺失或低分辨率：页面注明"图缺失/不可用"，`figure_model.availability` 如实标
  `missing`/`low_resolution`，记入 `rendering` 与 `uncertainty`，不生成伪造图。
- 字体/模板/office-renderer 不可用：退回纯文本大纲或 HTML deck，`status` 标
  `partial`，说明降级原因并写入 `degradations`。
- 导出失败或超时：保留可编辑源文件与部分结果，`status` 如实标 `partial`/`failed`，
  记入 `change_log`。
- 空输入目录：`scaffold` 写合法结果并标 `insufficient_evidence`，退出码 0。
- 缺失软件状态或科学数据一律只标 limitations / `pending_human`，禁止编造。

## 8. 脚本调用（相对路径，仅标准库）

```bash
python3 scripts/main.py scaffold --case /input/case.json --task /input/task.md --work /app/work --output /app/output
python3 scripts/main.py update   --result /app/output/result.json --plan /app/work/plan.json --figures /app/work/figure_model.json
python3 scripts/main.py validate --result /app/output/result.json --work /app/work --output /app/output --strict
python3 scripts/main.py summary  --result /app/output/result.json --plan /app/work/plan.json --figures /app/work/figure_model.json
python3 scripts/selftest.py
```

脚本只用 Python 标准库，路径相对 `SKILL.md` 所在目录；`main.py` 各子命令与
`selftest.py` 均可用 `--help` 查看参数。字段定义见 `references/output_contract.md`，
评分对应清单见 `references/checklist.md`。

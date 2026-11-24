---
name: academic-talk-organizer
description: 把论文或研究材料组织为证据可追溯、可编辑、符合模板的学术汇报：逐页唯一主张、图表引用、讲解顺序、讲稿备注、问答预演、页级证据映射与渲染检查；以 output_contract 为唯一契约源，result.json 字段逐一对齐并全程记录 change_log。适用于固定科研案例的幻灯片内容组织。
version: 2.0.0
---

# 学术汇报内容组织 Skill（契约对齐版）

## 1. 角色与何时使用

本 Skill 负责“从已有研究材料到可讲页面”的组织与交付，不负责论文精读、学术写作成稿与科研绘图本身的素材生产。任一条件满足即触发：

- 任务要求把论文 / 研究材料组织成演示文稿，并逐页标注主张、图表与证据来源；
- 输入含听众、时长、页数、模板等约束，需要内容取舍与叙事顺序设计；
- 评分要求证据可追溯、导出可打开、结论强度与证据匹配。

核心方法：先定报告目标与听众，再定“问题—方法—结果—结论”主线与页级取舍；每页一条唯一主张 + 支撑证据 + 证据强度；配套讲稿与问答准备；最后逐页回填来源定位并检查渲染导出。判断做对与否只看三件事：页上每个论断能否在源材料定位、叙事是否服务指定听众与时长、从源材料到成稿的取舍能否被复核。

## 2. 输入契约

运行时原始输入（只读，勿改）：

- `/input/case.json`：案例标识（`id` / `armiId`）、任务约束、材料清单；可能含 `output_contract`，若存在则以其中字段为准并保留全部字段（不改名、不删未知键）。
- `/input/task.md`：任务说明，可能给出听众 / 时长 / 页数 / 模板约束。
- 材料文件（PDF / 图片 / 模板 / 起始 deck）按 `case.json` 清单定位，通常位于 `/input/` 或 `/app/work/`；PDF 解析得到的真实内容才作为事实依据，不得凭标签补造。

可用工具：`pdf-text-tool`、`pdf-render-tool`、`ocr-tool`、`latex-compiler`、`office-renderer`、`browser-automation`、`font-discovery`、`scientific-python`。工作目录 `/app/work/`；最终产物写入 `/app/output/`。

## 3. 输出契约

必须产出 `/app/output/result.json`，顶层顺序固定为：

`case_id` → output_contract 全部字段 → `status` / `sources` / `uncertainty` / `pending_human` / `change_log`（附 `errors` / `degradations` / `runtime`）。

默认领域契约字段（字段定义与子结构见 `references/output_contract.md`，缺 `output_contract` 时启用）：

- `report_goal`：报告目标（可检查目标、听众、时长、页数、模板）。
- `audience`：听众先验、关注点、可能质疑。
- `narrative`：页面序列与取舍理由（非按论文目录搬运）。
- `pages`：页级结构数组，每页含 `page_index` / `title` / `single_claim` / `evidence_strength` / `figures` / `speaker_notes` / `source_refs` / `locator` / `quote`。
- `editable_deck`：可编辑演示文件（路径、格式、模板）。
- `evidence_map`：每页主张 → 来源定位（locator + 逐字 quote），未改写事实。
- `qa_prep`：预判追问、口径一致回答、可引用页面。
- `rendering`：源文件、导出件（PDF/PNG 预览）、字体、模板、修改记录。

**契约对齐规则（本版核心）**：`output_contract` 是唯一真源。`scripts/main.py bootstrap` 以默认契约 + case.json 的 `output_contract` 生成字段齐全的骨架；每字段在 result.json 中必须出现且只出现一次；未知键原样保留、缺值给 `null` / `[]` / `""`；`validate` 逐字段核对。可编辑演示文件（PPTX / HTML / ODP 等）与渲染预览（PDF / PNG）一并写入 `/app/output/`，未产出的字段留空或显式 `null`，不得编造。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

每步输出先落盘、后改进。**前 3 分钟（180 秒）内必须先写出一版字段齐全、状态为 `partial` 的 result.json 骨架**（步骤 2），此后只做增量改进；请求数为上限，不追求用完，到 850 秒即收尾。

| 步骤 | 输入 | 输出 | 时间预算 |
|-|-|-|-|
| 1 读取与核对 | case.json、task.md、材料 | 材料清单、约束、output_contract | ≤60s，3–5 请求 |
| 2 落盘骨架 | 步骤 1 的 case_id / 契约 | `main.py bootstrap` 生成 partial result.json + work/contract.json + sources.json | ≤120s，1–2 请求 |
| 3 主线与规划 | 材料全文 | report_goal / audience / narrative / pages 初稿 | 120–300s，8–15 请求 |
| 4 生成演示件 | pages + 模板 | 可编辑 deck + speaker_notes | 300–540s，10–20 请求 |
| 5 证据映射 | pages + 来源定位 | evidence_map，跑 `main.py evidence-check` | 540–660s，5–10 请求 |
| 6 渲染与自检 | deck + 导出件 | PDF/PNG 预览，跑 `main.py validate` | 660–780s，4–8 请求 |
| 7 增量收尾 | 自检报告 | 修复缺陷、更新 change_log | 780–900s，余量 |

每步用 `main.py merge`（`--set` / `--append` / `--update-file`）把新字段合并进 result.json，自动追加 `change_log`，保证骨架始终最新且可复核。

证据逐字比对约定：先用 `pdf-text-tool` 把 PDF 抽取文本存为 `/app/work/materials/*.md` 并写入 `result.sources`，`evidence_map` 的 `locator` 指向该文件（如 `materials/paper.md#p3`），`quote` 取自原文；`evidence-check` 据此做空白折叠后的子串匹配，PDF 抽取噪声导致的未命中记 warning 而非 error。

## 5. 自检清单（逐条可勾选，对应评分维度）

见 `references/checklist.md` 全表；交付前必须跑 `python3 scripts/selftest.py`（无外部文件、内存端到端自检）。

- [ ] 实际效果：report_goal、audience、narrative、pages、qa_prep 齐全且形成闭环（40 分）。
- [ ] 每页恰好一条 `single_claim`，无摘要逐段搬页；`evidence_strength` 与证据匹配。
- [ ] 页上数字、图表引用与源材料一致；覆盖论文主线、页结构、图表引用、讲解逻辑、模板约束、导出渲染六方面。
- [ ] 证据可核验：每页有 locator + 逐字 quote，`main.py evidence-check` 无 error。
- [ ] 使用增益：取舍决策写入 narrative/change_log，可复核；导出修正次数已记录。
- [ ] 创新增益：audience 影响页数/术语深度；pages 有唯一主张+证据+图表绑定；qa_prep 覆盖机制、数据、对照、局限四类追问。
- [ ] 可靠体验：可编辑文件 + PDF/PNG 预览存在，`main.py validate` 无阻断项。
- [ ] 异常处理：缺图 / 低分辨率 / 公式丢失 / 字体缺失 / 模板冲突 / 导出失败 / 超时均如实标注。
- [ ] 生态贡献：脚本仅标准库、相对路径调用，`--help` 可查用法，references 说明完整。

## 6. 降级策略（如实标注，不编造）

- 材料缺失或无法定位来源：保留 `single_claim`，`evidence_strength` 标 `low`，记入 `uncertainty`，不得补造出处。
- 图表缺失或低分辨率：页面注明“图缺失 / 不可用”，记入 `rendering` 与 `degradations`，不生成伪造图。
- 公式/符号丢失：在 speaker_notes 与 uncertainty 中注明“公式以原文为准”，不自行重排。
- 字体 / 模板 / office-renderer 不可用：退回 HTML deck 或纯文本大纲，`status` 标 `partial`，说明降级原因。
- 导出失败或超时：保留可编辑源文件与部分结果，`status` 如实标 `partial` / `failed`，记入 change_log。
- 缺失软件状态或科学数据：只标注 limitations，禁止编造评测分数或运行结果。

## 7. 脚本调用（相对路径，仅标准库）

```bash
python3 scripts/main.py bootstrap --case /input/case.json --task /input/task.md --work /app/work --output /app/output
python3 scripts/main.py merge --result /app/output/result.json --work /app/work --append pages='<页对象JSON>' --note "新增第 N 页"
python3 scripts/main.py evidence-check --result /app/output/result.json --work /app/work
python3 scripts/main.py validate --result /app/output/result.json --work /app/work
python3 scripts/selftest.py
```

脚本只依赖 Python 标准库，路径相对 `SKILL.md` 所在目录；均可用 `--help` 查看参数。字段定义见 `references/output_contract.md`，评分对应清单见 `references/checklist.md`。

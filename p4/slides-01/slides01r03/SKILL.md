---
name: slides-01
description: 论文转学术 PPT（slides-01）Skill。把论文/研究材料转成可讲、可核验、可编辑的学术演示：先由 Agent 真实阅读材料产出页级计划（每页一个 claim + role + 带 locator 与逐字 quote 的 evidence），再由确定性脚本按模板母版/版式继承生成可编辑 .pptx 与页数一致的 PDF 预览，并把七字段契约、证据映射、自检与不确定性写入 result.json。适用于"固定论文/材料 → 可交付学术汇报"的场合。
version: 0.3.0
---

# 论文转学术 PPT（slides-01）

## 0. 先做这 8 步（照做，不要先跑长流程）

1. `mkdir -p /app/work /app/output`；`cat /input/case.json`，记下 `case_id` 与 `output_contract` 字段名。
2. **立刻** `python3 -B scripts/main.py --skeleton-only --input /input --work /app/work --output /app/output`，先落盘合法 `result.json`（骨架也算可交接交付）。
3. `ls -R /input | head -100`；把材料按 pdf/text/pptx/image/data 分类，跳过 >2MB 的文本。
4. 真实阅读材料，把**每页主张、证据、逐字 quote** 写进 `/app/work/draft.json`；读不出就写缺口，不编造。
5. 把抽取出的原文段落落盘到 `/app/work/sources/<source_id>.txt`（供逐字 quote 校验）。
6. 运行 `python3 -B scripts/main.py --input /input --work /app/work --output /app/output --draft /app/work/draft.json --repro`，产出 `presentation.pptx` 与完整 `result.json`。
7. 用 office-renderer（LibreOffice headless）把 `.pptx` 导出为 `/app/output/presentation.pdf`，再带 `--pdf` 重跑一次核对页数。
8. 运行 `python3 -B scripts/selftest.py --root . --input /input --work /app/work --output /app/output`，逐条核对清单。

- 时间纪律：**前 3 分钟必须已落盘骨架**；每填一块就重写一次 `result.json`；之后只做增量改进，不推翻已冻结的 `draft.json`。
- 预算：900 秒 / 64 次模型请求 / 每次回复 16384 tokens（含推理）。**先交粗稿再精修**，不要一遍追求完美。

## 1. 角色与何时使用

把“论文/研究材料”转成**主线成立、页序可讲、证据可回溯、可编辑**的学术 PPT。使用时机：给定固定论文或汇报材料 + 可编辑母版模板 + 听众/时长/页数上限，需要交付可打开、可播放、可逐页编辑、可被他人按记录复跑的演示。不负责论文精读结论本身、不负责科研绘图美化、不负责学术写作润色。

判断做对的三个标准：主线与论文贡献一致而非按段落搬运；每页可回溯到论文的图/表/章节/数据；成品在指定模板与软件版本中可打开、可编辑、可复跑。

## 2. 输入契约

- 权威契约：`/input/case.json`（`case_id` + `output_contract`，字段名以它为准）+ `/input/task.md`（任务说明、约束）。
- 材料：论文 PDF/文本、模板 `.pptx`、图表、目标听众/时长/页数上限，位于 `/input` 下任意层级；**不假设固定文件名**，运行时实际扫描。
- 工作目录 `/app/work`；最终产物与 `result.json` 写入 `/app/output`。
- 读不到 `/input` 或坏 JSON：仍写 `result.json`，`status="input_error"`、`case_id="unknown-case"`，错误写入 `errors`。**没有 result.json 才是 0 分。**

## 3. 输出契约

`/app/output/result.json` 必须含：`case_id` + `output_contract` 的**全部原样字段**，再加信封字段 `status` / `sources` / `uncertainty` / `pending_human` / `errors` / `degradations` / `change_log` / `changes` / `runtime`。

关键字段语义：

| 字段 | 内容 |
|---|---|
| `case_id` / `status` | 输入标识；`ok\|partial\|insufficient_evidence\|input_error` |
| `editable_source` | `{path, format:"pptx", slides, sha256, inherited_template}` 指向真实可编辑 `.pptx` |
| `export_artifact` | `{pdf:{path, pages, sha256, matches_slides}}` 指向真实渲染 PDF（页数须与 slides 一致） |
| `content_map` | 每页 `{page, role, claim, title, layout, evidence:[{source_id, locator, quote, kind}]}` |
| `compliance_checks` | 逐条 `{id, check, status: pass\|fail\|skip\|warn, detail}` |
| `sources` | 材料清单 `{source_id, path, kind, size, sha256}` |
| `uncertainty` / `pending_human` | 证据缺口、未核验项、需人工确认项 |
| `change_log` / `changes` | 每次落盘/修改记录 `{ts, action, reason}` |
| `runtime` | 时间、`input_fingerprint`、`draft_fingerprint`、`output_fingerprint`、`deterministic_build` |

证据硬要求：每个关键结论/数值/图元带 `source_id + locator + quote`，quote 逐字命中材料；未报告写 `uncertainty`/`pending_human`，绝不编造数值、编号、DOI 或事实。

## 4. 执行流程（分步骤 + 时间预算）

| 步骤 | 动作 | 输入 → 输出 | 预算 |
|---|---|---|---|
| 1 | 建目录、读 case.json | `/input/case.json` → case_id + 契约字段 | ≤30s |
| 2 | 写骨架 | 契约字段 → `/app/output/result.json`（partial） | ≤60s，**3 分钟内必须完成** |
| 3 | 材料盘点 | `/input` 递归扫描 → 分类清单、指纹 | ≤60s |
| 4 | 读材料、写 draft | 论文/模板 → `/app/work/draft.json` + `sources/*.txt` | 剩余时间大头，≤5min |
| 5 | 确定性成稿 | draft+模板 → `presentation.pptx` + 完整 result.json | 脚本秒级 |
| 6 | 导出并核页数 | pptx → PDF（office-renderer）→ 重跑 `--pdf` | ≤90s |
| 7 | 自检 | 脚本+清单 → `selftest.py` 全绿 | ≤60s |

原则：步骤 2 后任何中断都不留空结果；步骤 4 写出的 `draft.json` **冻结后只增不改**，增量改进只允许补充 `uncertainty`/`change_log` 或补图，不重写已定稿页面的 claim/quote。

## 5. 确定性设计（本版重点）

- 脚本只做“机械布局”：由 `draft.json` + 输入文件唯一决定 `.pptx` 字节，ZIP 固定条目顺序、固定时间戳、固定压缩级别；`--repro` 时 result.json 时间戳固定，同输入两次产物字节一致。
- `runtime.input_fingerprint` / `draft_fingerprint` / `output_fingerprint` 三重指纹记录输入、草稿与成品，供 `selftest.py` 复跑比对。
- 内容不确定性被隔离在 `draft.json`（唯一由 Agent 写的内容层）；写完即冻结并加指纹，脚本不复写。
- 布局确定性：role → 版式选择有固定偏好序；每页最多 1 个 title 占位 + 1 个 body 占位 + N 个 pic 占位，文字自动 `normAutofit` 防溢出，不设 latin 字体让主题字体生效。

## 6. 交付前自检清单（对应评分维度，逐条可勾）

- [ ] `case_id` 非空且取自 case.json；`result.json` 含 output_contract 全部字段（完整性）
- [ ] `status` 诚实：材料不足写 `insufficient_evidence`，缺软件写 `partial`+`degradations`（异常处理）
- [ ] 每页 `claim` 单一明确，role 合法；主线必需页（问题/方法/证据/结论/限制）齐全（主线）
- [ ] 每页 evidence 有 `source_id + locator + quote`，quote 逐字命中 `work/sources/`（证据溯源）
- [ ] `presentation.pptx` 真继承模板母版/版式/主题，非“空白+贴文本框”（模板约束）
- [ ] `presentation.pdf` 页数与 slides 一致，`export_artifact` 指向真实文件（导出渲染）
- [ ] 数字/单位/方法名/结论与论文一致，未改写事实；示意图不被当作实验数据（正确性）
- [ ] `python3 -B scripts/selftest.py` 全部 PASS，`deterministic rebuild` PASS（确定性）
- [ ] 无占位空文件、无预制答案、无伪造分数（诚信）

## 7. 降级策略（不编造）

| 场景 | 处置 |
|---|---|
| 缺论文/材料 | `status="insufficient_evidence"`，缺口写 `pending_human` |
| 模板缺失 | 用内置最小母版成稿，`inherited_template=false` 写入 exceptions/degradations |
| 图无法提取/公式为图片/PDF 加密 | 记 `degradations`，不臆造图注与数值 |
| 无 office-renderer | 交付 `.pptx`+真实 SVG/PNG 预览，PDF 置 `pages=0` 并写 exceptions |
| 字体缺失 | 不指定 latin 字体，依赖模板主题字体；溢出靠 normAutofit |
| quote 未命中 | `compliance_checks` 记 fail，`uncertainty` 记 medium，不删证据 |

## 8. 脚本与资源

- `scripts/main.py`：契约骨架、指纹、draft 校验、确定性成稿、result.json/report.md（入口）。
- `scripts/pptx_core.py`：OOXML 构建/模板继承/校验/PDF 页数（纯标准库）。
- `scripts/selftest.py`：包结构、契约、pptx/pdf、draft、**确定性复跑**自检。
- `references/contract-checklist.md`：契约与证据映射细则；`references/determinism.md`：确定性与复跑规范。

依赖：仅 Python 3.8+ 标准库；脚本调用一律用相对路径 `scripts/main.py`（在包根目录下运行）。

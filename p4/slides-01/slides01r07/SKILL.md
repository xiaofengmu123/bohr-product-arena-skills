---
name: slides-01
description: 论文转学术 PPT（slides-01）Skill。从论文/研究材料提取主线、规划页级主张并保留证据映射，按给定模板生成可编辑 pptx 与页数一致的 PDF/预览，交付含 output_contract 全部字段的 result.json。本变体强化鲁棒性：空输入、坏 JSON、缺参数、超大文件均优雅降级、不崩溃、不编造。
version: 1.0.0-robust
---

# 论文转学术 PPT（slides-01）· 鲁棒性优先最短路径

## 0. 角色与何时使用
本 Skill 把一篇论文/研究材料转成**可讲、可核验、可编辑**的学术 PPT。适用于：有固定论文 PDF/汇报稿/大纲 + 模板 pptx + 目标听众/时长/页数上限，需要交付真实可编辑演示文件 + 渲染预览 + 页级证据映射的场景。不适用：一次性地贴段落、科研绘图美化、写作润色；这些不在本 Skill 范围。

判断是否做对只看三件事：主线与论文贡献一致（而非按段落顺序搬运）；每页可回溯到论文的图/表/章节/数据；成品能在指定模板与软件版本中打开、播放、编辑，并可按记录复跑出同一版式。

## 1. 输入契约
- 运行时原始输入在 `/input/`：`case.json`（含 `case_id` 与 `output_contract`）、`task.md`、论文/材料、模板 pptx、图/表等。**不要假设固定文件名**，先 `ls -R /input | head -120` 扫描。
- 权威契约是 `case.json` 的 `case_id` 与 `output_contract`；字段名以它为准，**原样回填全部键**，缺值给 `null`/`[]`，不改名、不删未知键。
- 工作目录 `/app/work/`（中间产物：`draft.json`、清单、缓存），最终产物写入 `/app/output/`。
- 材料逐字读取：数值、单位、方法名、图/表编号与正文引用必须与原文一致；读不出就写缺口，绝不补造。

## 2. 输出契约（result.json）
必须产出 `/app/output/result.json`，包含：`case_id` + `output_contract` 的全部字段 + 统一信封字段：

| 字段 | 含义 | 类型 |
|---|---|---|
| `case_id` | 复制自 `/input/case.json` | string |
| `status` | `ok \| partial \| insufficient_evidence \| input_error`，诚实填写 | string |
| `editable_source` | 主产物 pptx 的 path/format/slides | object |
| `export_artifact` | 渲染件 pdf/png 的 path/pages | object |
| `content_map` | 每页 `{page, claim, role, evidence:{locator, quote, figure_ref, table_ref, section_ref}, notes}` | list |
| `compliance_checks` | 逐条合规检查结果（页数一致/编号对得上/模板未覆盖等） | list |
| `exceptions` | 已知限制（缺字体/公式为图片/PDF 加密等） | list |
| `sources` | 材料来源清单（path + 类型 + 页数/字数） | list |
| `uncertainty` | 无法确认的项，逐条写 `not_reported`/`source_conflict` | list |
| `pending_human` | 需要人工复核或补料的项 | list |
| `change_log` | 带时间戳的修改记录 | list |
| `errors` / `degradations` / `runtime` | 错误、降级、运行信息 | list/list/object |

脚本 `scripts/main.py` 会自动回填 `output_contract` 字段并合并信封字段；不要手工删改未知键。**没有 result.json 才是 0 分**，其余降级都只是扣分。

## 3. 执行流程与时间预算
总预算 **900 秒 / 64 次模型请求 / 每响应 16384 tokens**。纪律：**每 40 秒至少写一次文件；不要连续思考超过 2 分钟；前 3 分钟必须先落盘一版结果骨架**，之后只做增量改进，先交粗稿再精修。

1. **落骨架（≤90 秒）**：`mkdir -p /app/output`；`cat /input/case.json` 记下 `case_id` 与 `output_contract` 字段名；立即运行 `python3 -B scripts/main.py --input /input --work /app/work --output /app/output`，它会先写出一版合法 `result.json`（status=`partial`，字段留空占位）。输入缺失/坏 JSON 时脚本会写 `status=input_error` 并记录原因，仍保证退出码 0。
2. **读材料（约 5 分钟）**：`ls -R /input`；读 task.md、论文 PDF（pdftotext）、模板 pptx 说明与图表；只读真实文件，不凭标签臆测内容。
3. **写主线计划（约 4 分钟）**：把研究问题→方法→结果→限制→结论写成页级计划，写入 `/app/work/draft.json`（见 §4 的 draft 结构）。每页一个主张，配 `role`、证据 `locator+quote`、图/表/章节引用、讲解备注。
4. **成稿与导出（约 3 分钟）**：用 python-pptx 或 `scripts/main.py --draft ...` 生成真实 pptx（优先继承模板母版/版式，禁止"新建空白+贴文本框"的伪继承）；导出 PDF，**页数必须与 pptx 页数一致**。
5. **复核（约 1 分钟）**：运行 `python3 -B scripts/selftest.py`；逐条勾选 §6 清单；每填一部分就重写一次 `result.json` 保持可交接。剩 5 分钟必须已落盘可打开产物。

**draft.json 结构**（Agent 真实阅读材料后自行撰写，脚本不替你组织内容）：
```json
{"case_id":"...","audience":"...","duration_minutes":0,"slide_cap":0,"title":"...",
 "slides":[{"id":1,"claim":"一页一个主张","role":"background|method|result|discussion|limitation|conclusion",
   "bullet_points":["短语级要点"],"evidence":{"locator":"paper.pdf#p3","quote":"逐字命中原文",
   "figure_ref":"Fig. 2","table_ref":null,"section_ref":"3. Results"},
   "speaker_notes":"与页面不重复的讲解备注/过渡/被问补充"}]}
```

## 4. 证据溯源与写作规则
- 每个关键结论/数值/图元都要带 `locator + 逐字 quote`；quote 必须逐字命中材料，禁止意译改写事实。
- 冲突信息不隐藏：同一指标两处不一致时都记录并标 `source_conflict`。
- 讲解备注与页面文字**不重复**：写过渡、强调点、被问到的补充证据，而不是复制 bullet。
- 页级主张要短：一页一个主张，长句改写为短语；控制单页密度，长标题/多图/大表格需适配模板占位符。
- 区分主结论与支撑细节；示意图不得当实验数据展示；他人结果不得当本文结果。

## 5. 脚本调用（相对路径，均纯标准库）
- `python3 -B scripts/main.py --help` —— 查看参数（`--input/--work/--output/--draft`，缺省 `/input`、`/app/work`、`/app/output`）。
- `python3 -B scripts/main.py --input /input --work /app/work --output /app/output` —— 发现材料、回填契约、落骨架、按 draft 构建 pptx/PDF。
- `python3 -B scripts/selftest.py` —— 自检（见 §6）。
脚本只作**单次工具**，不要串成多步流水线；读材料、抽主张、写 draft、做幻灯片的主体工作由 Agent 自己完成。

## 6. 自检清单（逐条勾选，对应评分维度）
- [ ] `result.json` 存在且为合法 JSON，含 `case_id` 与 `output_contract` 全部字段 + 信封字段（§2）。
- [ ] `status` 诚实；缺材料= `insufficient_evidence`，缺输入= `input_error`，有缺口= `partial`。
- [ ] 主线页齐备（问题/方法/结果/限制/结论），页序可讲，非段落搬运。
- [ ] 每页 `content_map` 有 locator + 逐字 quote，图/表/章节编号与正文引用对得上。
- [ ] 讲解备注存在且不重复页面文字。
- [ ] 真实 pptx 可打开、可编辑；继承模板母版/版式/主题（非伪继承）。
- [ ] 真实 PDF/PNG 预览存在，页数与 pptx 一致。
- [ ] `sources`/`uncertainty`/`pending_human`/`change_log`/`errors`/`degradations` 完整、真实。
- [ ] 缺数据/缺软件时如实写 `limitations`/`exceptions`，无任何编造。

## 7. 降级策略（不编造）
- 缺输入/坏 JSON → 脚本写 `status=input_error`、`case_id=unknown-case`，`errors` 记原因，退出码 0。
- 空材料 → `status=insufficient_evidence`，`pending_human` 列出缺口。
- 单文件 >2MB → 跳过并记 `degradations`，不读入内存导致 OOM。
- 无 python-pptx/LibreOffice → 脚本用纯标准库构建最小可编辑 pptx 或 SVG 预览，并在 `exceptions` 标注。
- 字体缺失/公式为图片/PDF 加密/模板占位符不足/页数超限 → 逐条识别，写入 `exceptions` 或 `pending_human`。
- 写盘失败 → 降级到最小合法产物，进程不崩溃。

## 8. 硬性红线
- 绝不编造数值、单位、图表编号、DOI、引用、评分或运行结果；缺就写 `not_reported`/`pending_human`。
- 禁止占位空文件冒充产物；禁止把示例答案当正式结果。
- 运行时脚本路径用相对路径 `scripts/main.py`，保证可执行。

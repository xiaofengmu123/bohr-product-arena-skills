---
name: academic-slides-ir-compile
description: 学术 PPT 模板与格式适配：编译器式流水线，以 Deck IR 为单一事实源，把论文材料真继承套入给定模板（母版/版式/主题指纹可核），产出可编辑 pptx + 页数一致 PDF/PNG + 逐页证据映射，并回读产物做 round-trip 审计。覆盖模板套用、版式约束、字体颜色、图表适配、批量一致性与导出渲染六类要素。
version: 0.2.0
---

# 学术 PPT 模板与格式适配（Deck IR 编译器）

## 1. 角色与何时使用

你是"学术汇报编译器"：给定论文/科研材料与一个带母版/版式的模板，交付**可打开、可继续编辑、证据可追溯、导出可复核**的演示文稿。核心判据（不看页数）：① 模板被真实套用（母版/版式/主题 sha256 与模板一致、新页 rels 指向模板既有版式）；② 页内文字/数字/图号与源材料一致；③ 整册版式与导出件可被他人复跑逐页核对。

**何时使用**：输入为 `/input/case.json` + `/input/task.md` 且要求生成 PPTX 时启用。每个案例从实际文件现做，不用预制答案。与参考实现的关键区别：**先编译成 Deck IR（单一事实源），pptX / evidence_map / report 全部从同一份 IR 派生，编译后再回读产物做 round-trip 审计**，而不是直接从手写稿一次性贴出 pptx。

## 2. 输入契约

- 原始输入在 `/input/case.json`（含 `case_id`、`task_instruction`、可能的 `output_contract`、`materials`）与 `/input/task.md`；模板 pptx 与论文/图表按题面路径提供。
- PDF 以**原文件**给出，无预提取答案层。**读真实文件**，不得按标签脑补内容；PDF 先用 pdf-text-tool/OCR 提取到 `/app/work/extracted/*.txt` 再引用。
- 工作区 `/app/work/`（提取文本、`deck.json` IR、lint/audit 报告）；最终产物写 `/app/output/`。
- 可编辑 Office/slide 产物**必须**同时给真实渲染的 PDF/PNG 预览；office-renderer 可用时优先用它渲染权威 PDF/PNG，脚本内置文本层 PDF 与结构 PNG 仅为兜底。

## 3. 输出契约

必须产出 `/app/output/result.json`，**原样回填 `case.json` 的 `output_contract` 全部键**（缺值给 `null`/`[]`，不改名、不删未知键），并补齐统一信封字段：

- 七个标准契约键：`case_id` / `status` / `editable_source` / `export_artifact` / `content_map` / `compliance_checks` / `exceptions`（字段详义见 `references/contract.md`）。
- 统一信封：`sources`（材料清单）/ `uncertainty` / `pending_human` / `change_log` / `errors` / `degradations` / `runtime`。
- `status` 取值：`ok | partial | insufficient_evidence | input_error`；`content_map` 每页 `{n,title,claim,role,evidence[{source_id,locator,quote,quote_status}],assets,notes}`。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求 / 单响应 ≤16384 token）

**硬规则：前 3 分钟内必须先跑 `python scripts/main.py init` 落盘一版 partial 结果骨架，之后只做增量改进；任何一步失败都保留已落盘的部分结果。**

| 阶段 | 时间 | 动作（输入→输出） |
|---|---|---|
| 0 init | 0–3 min | 读 `/input/case.json`+`task.md`，清点文件；`python scripts/main.py init --input /input --work /app/work --output /app/output` → 落 `result.json`(status=partial) + `index.json` + `deck.skeleton.json` |
| 1 提取 | 3–8 min | 用 pdf-text-tool/pdftotext 提取论文到 `/app/work/extracted/*.txt`；OCR 处理扫描页；font-discovery 查字体 |
| 2 计划 | 5–10 min | 从主线提炼研究问题/方法/结果/限制，把页级主张、图引用、讲解顺序、版式写入 `/app/work/deck.json`（IR） |
| 3 lint | 8–10 min | `python scripts/main.py lint` → 校验 IR 结构、角色覆盖、证据逐字命中、页数-时长比 |
| 4 compile | 10–13 min | `python scripts/main.py compile` → IR→模板绑定→pptx/pdf/png/evidence_map/template_map/report/result.json |
| 5 audit | 13–15 min | `python scripts/main.py audit`（回读产物 round-trip）与 `selfcheck`；修溢出/字体/图号，反复 ≤2 轮 |

请求纪律：文本只提取一次并缓存到 `/app/work`，勿反复重读整篇；每步 1–2 次请求，留余量给返工。脚本路径用相对路径 `scripts/xxx.py`，从 Skill 根目录调用（评测装在 `/skills/weekly-skill`，先 `cd` 到安装目录）。

## 5. 自检清单（逐条勾选，对应评分维度）

逐页完成后按 `references/checklist.md` 勾选；未满足项写入 `uncertainty`，不假装通过。脚本化自检跑 `python scripts/selftest.py`（内部合成 fixture，验证 init→lint→compile→audit→selfcheck 全链路、确定性、降级路径）。

- **实际效果(40)**：模板真实套用（母版/主题 sha256 一致）；数字图号一致；封面→结论页型齐全；可编辑 pptx+真实预览；逐页偏差可复跑核对。
- **使用增益(25)**：先骨架后增量减少返工；`runtime` 记录实际耗时/步骤数，`change_log` 记改动页。
- **创新增益(15)**：IR 单一事实源 + round-trip 审计；字号间距阶梯与图表等比缩放规则可解释；用 `audit` 证明哪些能自动测、哪些只能人工核。
- **可靠体验(10)**：路径/顺序/时间预算写清；缺版式、溢出、字体缺失、图表失效、导出失败均有处置；重复运行幂等（zip 固定时间戳、parts 排序）。
- **生态贡献(10)**：`references/contract.md` 契约 + `references/checklist.md` 清单 + 标准库脚本可复用。

## 6. 降级策略（诚实 > 完整）

- **缺输入文件/缺模板版式/缺字体/图表源失效/导出失败**：在 `uncertainty` 与 `exceptions` 逐条写 `{item,reason,impact}`，`status` 降为 `partial`/`insufficient_evidence`/`input_error`，`change_log` 记原因与时间戳。
- 模板缺所需 layout：按角色选最接近版式，替代关系记入 `template_map.json` 的 `substitutions`，**不得谎称"已继承"**。
- 字体缺失：用 font-discovery 查可用字体，回退记入 `exceptions`；脚本 OOXML 用 `+mj-lt/+mn-lt`（主题字体）与 `schemeClr`（主题色），不硬编码字体名。
- 无可用模板：回退内置母版生成真实 pptx，但 `template_inheritance` 检查记为 fail 并写例外，**不伪造继承**。
- 图源失效/权威导出失败：如实保留 null 路径并说明；**不伪造文件、不编造运行分数或软件状态**。
- 部分结果也要完整落盘：init 阶段就写好所有字段（空值 null/[]），后续只填充。

## 7. 脚本与资源

- `scripts/main.py`：CLI 编排（init/survey/lint/compile/audit/selfcheck），薄入口，含紧急兜底（退出码恒 0，错误写 result）。
- `scripts/core.py`：Deck IR、证据引擎、lint、模板检查/绑定、OOXML/PDF/PNG 导出、round-trip 审计。
- `scripts/selftest.py`：自检（合成 fixture 全链路 + 确定性 + 降级）。
- `references/contract.md`：result.json 与 Deck IR 全字段说明；`references/checklist.md`：自检清单与降级矩阵。

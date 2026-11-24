---
name: latex-writing-compile
description: 面向 LaTeX 学术写作与编译的可复核、确定性 Skill：读取科研材料与任务约束，生成或修改可编译 .tex 稿件，循环编译并定位/分类/修复错误，校验公式/表格/引用/模板必填项，建立事实-来源映射，产出可编辑产物与 /app/output/result.json。当任务要求以 LaTeX 完成写作、改写、模板适配、公式或引用修复、可重复构建时使用。
version: 2.0.0
---

# LaTeX 写作与编译 Skill（确定性 + 自检强化版）

## 1. 角色与何时使用

把「科研材料 → 可编译 .tex 稿件 → 可复核结果」沉淀为可重复、可复现的流程。适用：依据材料生成/改论文、迁入指定模板、修复公式/表格/交叉引用/参考文献、定位编译错误并稳定构建。不适用：纯聊天问答、与 LaTeX 无关的写作。

核心原则：**先编译再谈对错；事实有出处，缺口显式标注；同输入同环境两次运行产物一致**。数字、单位、引用逐字复用材料；材料没有的数值写 `MATERIAL GAP` 或 `pending_human`，绝不编造。

本版相对 1.0 的核心升级是**自检与确定性**：新增 `scripts/selftest.py`、可勾选清单与确定性序列化，保证同一输入两次运行得到字节级一致的 result.json 与主产物。

## 2. 输入契约（先发现，不假设）

运行时输入固定为：
- `/input/case.json`：案例 id、任务说明、材料路径、`output_contract`（须原样回填的字段清单）与证据范围；
- `/input/task.md`：任务与格式约束；
- `/input/` 下任意材料（`materials/`、`*.md`、`*.tex`、`*.bib`、`*.pdf`、图片等）。**不假设固定文件名或键名**，先 `bootstrap` 扫描。

PDF 必须用平台 `pdf-text-tool`（pdftotext）解析实际文件，不得按文件名/领域标签补造内容。工作目录 `/app/work`（中间产物），最终产物写 `/app/output/`。

## 3. 输出契约

必须产出 `/app/output/result.json`，结构为：

```json
{
  "case_id": "...",
  "<output_contract 的每个键>": "<实际值>",
  "status": "ok | partial | insufficient_evidence | input_error",
  "artifacts": {"main_tex": "...", "bib": "...", "cls": "...", "pdf": "...", "compile_report": "..."},
  "sources": [{"locator": "materials/paper.md#L12", "quote": "逐字片段", "usage": "...", "conflict": false}],
  "uncertainty": [],
  "pending_human": [],
  "change_log": [{"at": "<run_id>", "step": "...", "note": "..."}],
  "errors": [], "degradations": [], "limitations": [],
  "runtime": {"run_id": "...", "deterministic": true, "producer": "latex-writing-compile", "schema_version": "2.0.0"}
}
```

- `case_id` 取自 case.json（键可能是 `case_id`/`id`/`challenge_id`/`benchmarkId`/`armiId`，缺失用 `unknown` 并标 `input_error`）。
- `output_contract` **全部键原样回填**：不改名、不删未知键；未完成给 `null` 或 `[]`，不得省略。
- `status` 诚实：材料不足=`insufficient_evidence`；部分完成=`partial`；全部满足=`ok`；输入无法解析=`input_error`。
- `sources` 每条含 `locator` + 逐字 `quote`；冲突来源都保留并标 `source_conflict`。
- `change_log` 的 `at` 用 `run_id`（对 /input 内容求 SHA-256 的确定性 token），**禁止写墙钟时间**，否则破坏确定性。
- 可编辑主产物（`.tex`）及配套 `.bib`/`.cls`/图片放入 `/app/output/`；编译成功时随附 PDF 预览；`compile_report.json` 记录命令、引擎与轮次。中途异常不得留下不完整 result.json。

## 4. 执行流程（总预算：900 秒、64 次模型请求、单次 ≤16384 tokens）

**硬性要求：前 3 分钟内必须先落盘一版 result.json 骨架，之后只做增量改进。**

| 阶段 | 时间 | 请求 | 动作与输入/输出 |
|---|---|---|---|
| P0 引导 | 0–60s | ≤3 | `python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work`。落盘 result.json 骨架 + `/app/work/inventory.json`；再读 case.json/task.md 确认任务类型。 |
| P1 初稿 | 60–180s | ≤15 | 提取材料事实池（数字/单位/引用/图），确定体裁与模板。生成可编辑 `main.tex` + `.bib`，回填各 contract 字段为初稿值。 |
| P2 编译-修复 | 180–600s | ≤30 | 首选平台 `latex-compiler` 工具编译；本地兜底 `python3 scripts/main.py compile --main ... --outdir /app/output`。循环编译；每条报错用 `classify-log` 定位 文件:行号 + 分类 + 根因再修，不做盲改。`refcheck` 校验 label/ref/cite。 |
| P3 核验溯源 | 600–870s | ≤10 | `main.py selfcheck`；逐条核对关键事实（`evidence quote` 取逐字片段），填 `sources`/`uncertainty`/`pending_human`/`change_log`；可编译则附 PDF。 |
| P4 收尾 | 870–900s | ≤6 | `main.py finalize` + `selfcheck` + `scripts/selftest.py determinism`；保证 result.json 合法、contract 键齐全、两次运行一致。 |

降级触发：剩余请求 <10 或剩余时间 <120s 时，立即 `finalize` 并诚实标 `partial`/`insufficient_evidence`，列 `pending_human`。脚本调用用相对路径 `scripts/*.py`；若 cwd 不是 skill 根目录，回退到 `/skills/weekly-skill/scripts/*.py`。

## 5. 自检清单（逐条勾选，对应评分维度）

**实际效果（编译得通/改得对/交得可用/复现得了）**
- [ ] main.tex 在目标模板下 `-halt-on-error` 至少一次编译通过（或记录首个未被修复的根因，文件:行号）。
- [ ] 每条报错都有 文件:行号 + 分类 + 根因说明，未停留在第一条错误信息。
- [ ] 公式符号/上下标/正斜体、表格数值与列宽、`\ref`/`\cite` 与原文意图一致。
- [ ] 模板必填项（作者/标题/摘要/关键词/引用样式/文件结构）逐项覆盖。
- [ ] 交付物是可编辑 `.tex`，非 PDF/截图；记录 TeX 发行版、宏包、编译命令与轮次。

**结果可核验性 / 使用增益**
- [ ] result.json 含 case_id、status、sources、uncertainty、pending_human、change_log，且 contract 键齐全。
- [ ] 每个关键数值/引用有 locator + 逐字 quote；无材料外新事实。
- [ ] 改动有 diff/说明可回滚；编译过程与产物可被第三方复核。

**可靠体验 / 异常处理**
- [ ] 空输入/坏 JSON/坏编码：退出码 0，写 `insufficient_evidence` 或 `input_error`，不崩溃。
- [ ] 缺宏包/缺字体/报错级联/模板类缺失：分类记录到 `errors`/`degradations`/`limitations`，继续处理其余材料。
- [ ] 重复运行结果一致：`scripts/selftest.py` 全部 PASS（确定性）。

## 6. 降级策略（如实标注，绝不编造）

- 缺 LaTeX 引擎/宏包/字体：`compile` 记录 `engine_missing`，result.json 写 `degradations` 与 `limitations`，**不得声称编译成功**；仍交付可编辑 `.tex` 与诊断报告。
- 材料不足/冲突：写 `insufficient_evidence` 或 `source_conflict` + `pending_human`，绝不补造数值、DOI、作者或实验结果。
- 无法解析 case.json 的 output_contract：骨架仍含 case_id/status/sources/uncertainty/change_log，标记 `input_error` 并说明缺失字段。
- 写盘失败：降级到最小合法 result.json（元数据 + 缺口说明），不让进程崩溃。
- 任何情况下 `status`、`limitations` 与实际能力一致；不伪造软件状态、编译结果或评测分数。

## 7. 脚本接口（相对路径，仅标准库）

```bash
python3 scripts/main.py bootstrap   --input /input --output /app/output --work /app/work
python3 scripts/main.py discover    --input /input
python3 scripts/main.py finalize    --input /input --output /app/output --work /app/work
python3 scripts/main.py selfcheck   --input /input --output /app/output --work /app/work
python3 scripts/main.py manifest    --input /input --output /app/output
python3 scripts/main.py compile     --main MAIN.tex --outdir /app/output [--passes 3] [--engine auto]
python3 scripts/main.py classify-log --log LOG [--main MAIN.tex]
python3 scripts/main.py refcheck    --dir DIR [--bib BIB]
python3 scripts/main.py check-tex   --main MAIN.tex [--bib BIB]
python3 scripts/main.py evidence index --root /input --out /app/work/source_index.json
python3 scripts/main.py evidence quote --file PATH --start N --end M
python3 scripts/selftest.py         # determinism / robustness / contract
```

全部仅用 Python 标准库；`--help` 可查看参数。示例输出仅出现在 `references/`，不得冒充正式案例结果。

## 8. 确定性约定（本版重点）

- 所有遍历排序、JSON `sort_keys=True`、无墙钟时间、无随机数；`run_id` = SHA-256(排序后的输入文件内容) 前 16 位。
- agent 写 change_log 时只写 `step`/`note`，由 `finalize` 统一回填 `at`（=run_id）。
- 交付前必须运行 `python3 scripts/selftest.py`，`determinism` 检查失败时定位并修复源头，不得忽略。

## 9. 最小调用示例（示例，非正式案例）

输入：`/input/case.json`（含 `output_contract: {"title": "…", "sections": []}`）、
`/input/task.md`、`/input/materials/paper.md`。

```bash
python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work
python3 scripts/main.py evidence index --root /input --out /app/work/source_index.json
python3 scripts/main.py evidence quote --file /input/materials/paper.md --start 12 --end 14
# agent 生成 main.tex / main.bib，回填 result.json 各 contract 字段
python3 scripts/main.py compile --main /app/output/main.tex --outdir /app/output --passes 3
python3 scripts/main.py check-tex --main /app/output/main.tex --bib /app/output/main.bib
python3 scripts/main.py finalize --input /input --output /app/output --work /app/work
python3 scripts/main.py selfcheck --input /input --output /app/output --work /app/work
python3 scripts/selftest.py
```

示意输出（`selfcheck`，均为「示例」，非正式案例结果）：

```text
- [x] contract_keys_complete — contract 键缺失: 无
- [x] sources_have_locator_quote — 每条 source 均含 locator + 逐字 quote
- [x] determinism_marked — runtime.deterministic 标记为 true
16 checks, 0 failed
```

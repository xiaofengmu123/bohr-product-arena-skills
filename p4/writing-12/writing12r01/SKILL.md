---
name: latex-evidence-write
description: 面向 LaTeX 学术写作与编译、并以证据溯源为核心的 Skill。读取科研材料与任务约束，生成或修改可编译 .tex 稿件，循环编译并分类、定位、修复错误，校验公式/表格/引用/模板必填项；每个关键结论、数值、术语、引用都建立 locator + 逐字 quote 的 evidence_map，冲突与缺口显式标注，产出可编辑产物与 /app/output/result.json。当任务要求以 LaTeX 完成写作、改写、模板适配、公式或引用修复、可重复构建，且要求结果可逐条回溯来源时使用。
version: 1.1.0
---

# LaTeX 写作与编译 Skill（证据溯源强化版）

## 1. 角色与何时使用

把「科研材料 → 可编译 .tex 稿件 → 可复核结果」沉淀为可重复流程。适用：依据材料生成/改写论文章节、迁入指定模板、修复公式/表格/交叉引用/参考文献、定位编译错误并稳定构建。

核心原则：**先编译，再谈对错；事实有出处，每条结论都有 locator + 逐字 quote；缺口显式标注，绝不编造**。数字、单位、术语、引用逐字复用材料；材料没有的数值补 `MATERIAL GAP`，状态标 `gap`；需人工确认的标 `pending_human`。不适用：纯聊天问答、与 LaTeX 无关的写作。

## 2. 输入契约

运行时输入固定为：
- `/input/case.json`：案例 id、任务说明、材料路径、`output_contract`（须原样回填的字段清单）与证据范围；
- `/input/task.md`：任务与格式约束；
- `/input/` 下任意材料（`materials/`、`*.md`、`*.tex`、`*.bib`、`*.pdf`、图片等）。**不假设固定文件名或键名**，先 `discover` 扫描发现。

PDF 材料必须用平台 `pdf-text-tool`（pdftotext）解析实际文件，不得按文件名/领域标签补造内容。工作目录 `/app/work`（中间产物），最终产物写 `/app/output/`。

## 3. 输出契约

必须产出 `/app/output/result.json`：

```json
{
  "case_id": "...",
  "<output_contract 的每个键>": "<实际值>",
  "status": "ok | partial | insufficient_evidence | input_error",
  "artifacts": [{"path": "main.tex", "kind": "tex", "role": "primary"}],
  "evidence_map": {"claims": [], "conflicts": []},
  "sources": [],
  "uncertainty": [],
  "pending_human": [],
  "change_log": [],
  "errors": [],
  "degradations": []
}
```

- `case_id` 取自 case.json（键名可能是 `case_id`/`id`/`challenge_id`/`benchmarkId`/`armiId`，缺失用 `unknown` 并标 `input_error`）。
- `output_contract` 的**全部键必须原样回填**：不改名、不删未知键；未完成值给 `null` 或 `[]`，不得省略。
- `status` 诚实：材料不足=`insufficient_evidence`；部分完成=`partial`；全部满足=`ok`；输入无法解析=`input_error`。
- `artifacts`：主产物（可编辑 `.tex`）及配套 `.bib`/`.cls`/图片的路径、类型与角色；编译成功时附 PDF 预览。
- `evidence_map.claims`：每条关键结论一个条目，含 `id`、`type`（number/term/citation/figure/claim/edit）、`statement`（稿件断言）、`locator`、`quote`（逐字原文）、`status`（verified/pending_human/conflict/gap）、`confidence`。
- `evidence_map.conflicts`：同一指标多来源不一致时全部保留并标 `source_conflict`。
- `sources`：每条含 `locator` + 逐字 `quote`；`uncertainty`：缺口、冲突、待确认项；`pending_human`：须人工决定项；`change_log`：每步改动时间戳与说明。
- 中途异常不得留下不完整 result.json（先落盘骨架，再增量改进）。

## 4. 执行流程（总预算：900 秒、64 次模型请求、单次 ≤16384 tokens）

**硬性要求：前 3 分钟内必须先落盘一版 result.json 骨架，之后只做增量改进。**

| 阶段 | 时间 | 请求预算 | 动作与输入/输出 |
|---|---|---|---|
| P0 引导 | 0–60s | ≤3 | `python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work`：立即写 result.json 骨架（contract 键默认 null + evidence_map 空骨架）+ `/app/work/inventory.json`。再读 case.json/task.md 确认任务类型与 output_contract。 |
| P1 证据与初稿 | 60–180s | ≤15 | `python3 scripts/evidence.py index --root /input --out /app/work/source_index.json`；对材料事实池逐条抽取（数字/单位/术语/引用/图），每条配 `locator + quote` 并 `evidence.py verify` 校验逐字一致。确定体裁与模板，生成可编辑 `main.tex` + `.bib`，回填 contract 字段为初稿值并写 evidence_map。 |
| P2 编译-修复 | 180–600s | ≤30 | `python3 scripts/latex_tools.py compile --main main.tex --outdir /app/output` 循环编译；`classify-log` 对每条报错给 `文件:行号 + 分类 + 根因 + 修复策略`，逐条修复而非盲改；`refcheck` 校验 label/ref/cite。每轮写 `compile_report.json`。 |
| P3 核验与溯源 | 600–870s | ≤10 | `python3 scripts/main.py selfcheck ...`；`evidence.py verify-map` 逐条验证每个 claim 的 quote 是材料原文子串；填 `sources`/`uncertainty`/`pending_human`/`change_log`；可编译则附 PDF。 |
| P4 收尾 | 870–900s | ≤6 | `python3 scripts/main.py finalize ...` + `selfcheck`；保证 result.json 合法、contract 键齐全、evidence_map 通过逐字校验。 |

降级触发：剩余请求 <10 或剩余时间 <120s 时，立即 `finalize` 并诚实标 `partial`/`insufficient_evidence`，把未决项写入 `pending_human`。脚本一律相对路径 `scripts/evidence.py`；若 cwd 不是 skill 根目录，回退到 `/skills/weekly-skill/scripts/evidence.py`。

## 5. 自检清单（逐条勾选，对应评分维度）

**实际效果（编译得通/改得对/交得可用/复现得了）**
- [ ] main.tex 在目标模板下 `-halt-on-error` 至少一次编译通过（或记录首个未修复根因）。
- [ ] 每条报错都有 文件:行号 + 分类 + 根因说明，未停留在第一条错误信息。
- [ ] 公式符号/上下标/正斜体、表格数值与列宽、`\ref`/`\cite` 与原文意图一致。
- [ ] 模板必填项（作者/标题/摘要/关键词/引用样式/文件结构）逐项覆盖。
- [ ] 交付物是可编辑 `.tex`，非 PDF/截图；记录 TeX 发行版、宏包、编译命令与轮次。

**结果可核验性 / 证据溯源（本版重点）**
- [ ] result.json 含 case_id、status、artifacts、evidence_map、sources、uncertainty、pending_human、change_log，且 contract 键齐全。
- [ ] evidence_map 每条 claim 有 locator + 逐字 quote，且 `verify-map` 证明 quote 是材料原文子串。
- [ ] 无材料外新事实；冲突来源标 `source_conflict` 并全部保留。
- [ ] 改动有 diff/说明，可回滚；编译过程与产物可被第三方复核。

**可靠体验 / 异常处理**
- [ ] 空输入/坏 JSON/坏编码：退出码 0，写 `insufficient_evidence` 或 `input_error`，不崩溃。
- [ ] 缺宏包/缺字体/报错级联/模板类缺失：分类记录到 `errors`/`degradations`，继续处理其余材料。
- [ ] 重复运行结果一致（固定编译命令与轮次、确定性输出、不依赖时钟除 change_log 时间戳外）。

## 6. 降级策略

- 缺 LaTeX 引擎/宏包/字体：`latex_tools.py` 记录 `engine_missing`，result.json 写 `degradations` 与 `limitations`，**不得声称编译成功**；仍交付可编辑 `.tex` 与诊断报告。
- 材料不足/冲突：写 `insufficient_evidence` 或 `source_conflict` + `pending_human`，绝不补造数值、DOI、作者或实验结果。
- 无法解析 case.json 的 output_contract：骨架仍含 case_id/status/sources/uncertainty/change_log/evidence_map，标 `input_error` 并说明缺失字段。
- 写盘失败：降级到最小合法 result.json（元数据 + 缺口说明），不让进程崩溃。
- 每条 quote 若无法在源文件定位到逐字匹配，则降级为 `gap` 或 `pending_human`，并在 `uncertainty` 记录，绝不保留未经验证的 quote。

## 7. 脚本接口（相对路径）

```bash
python3 scripts/main.py bootstrap   --input /input --output /app/output --work /app/work
python3 scripts/main.py discover    --input /input
python3 scripts/main.py finalize    --input /input --output /app/output --work /app/work
python3 scripts/main.py selfcheck   --input /input --output /app/output --work /app/work
python3 scripts/evidence.py index    --root /input --out /app/work/source_index.json
python3 scripts/evidence.py quote    --file PATH --start N --end M
python3 scripts/evidence.py verify   --file PATH --quote TEXT
python3 scripts/evidence.py verify-map --map /app/work/evidence_map.json --root /input
python3 scripts/latex_tools.py compile   --main MAIN.tex --outdir /app/output [--passes 3]
python3 scripts/latex_tools.py refcheck  --dir DIR [--bib BIB]
python3 scripts/latex_tools.py classify-log --log LOG [--main MAIN.tex]
python3 scripts/selftest.py --work ./selftest_work
```

全部仅用 Python 标准库；`--help` 可查看参数。示例输出仅出现在 `references/`，不得冒充正式案例结果。

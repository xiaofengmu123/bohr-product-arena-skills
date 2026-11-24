---
name: latex-writing-compile-budgeted
description: 面向 LaTeX 学术写作与编译的可复核 Skill（时间预算变体）。读取科研材料与任务约束，生成/修改可编译 .tex 稿件，循环编译并分类、定位、修复错误，校验公式/表格/引用/模板必填项，建立事实-来源逐字映射，在 900 秒/64 请求预算内先落盘 partial 骨架再增量改进，产出可编辑产物与 /app/output/result.json。当任务要求以 LaTeX 完成写作、改写、模板适配、公式或引用修复、可重复构建时使用。
version: 1.0.0
---

# LaTeX 写作与编译 Skill（时间预算·早落盘变体）

## 1. 角色与何时使用

把「科研材料 → 可编译 .tex 稿件 → 可复核结果」沉淀为可重复流程。适用：依据材料生成/改写论文章节、迁入指定模板、修复公式/表格/交叉引用/参考文献、定位编译错误并稳定构建。不适用：纯聊天问答、与 LaTeX 无关的写作任务。

核心原则：**先编译，再谈对错；事实有出处，缺口显式标注，绝不编造**。数字、单位、引用逐字复用材料；材料没有的数值补 `MATERIAL GAP`，或写入 `pending_human`。本变体的设计取舍：把 900 秒/64 请求拆成六阶段硬预算，**前 3 分钟内必须落盘一版合法 partial 骨架**，之后任何阶段只做单调增量改进——任何中断都能留下可加载、诚实的结果，而不是空目录或半截 JSON。

## 2. 输入契约

运行时输入固定为：
- `/input/case.json`：案例 id、任务说明、材料路径、`output_contract`（须原样回填的字段清单）与证据范围；
- `/input/task.md`：任务与格式约束；
- `/input/` 下任意材料（`materials/`、`*.md`、`*.tex`、`*.bib`、`*.pdf`、图片等）。**不要假设固定文件名或键名**，先扫描发现。

PDF 材料必须用平台 `pdf-text-tool`（pdftotext）解析实际文件，不得按文件名/领域标签补造内容。工作目录 `/app/work`（中间产物），最终产物写 `/app/output/`。案例 id 键名可能是 `case_id`/`id`/`challenge_id`/`benchmarkId`/`armiId`，缺失用 `unknown` 并标 `input_error`。

## 3. 输出契约

必须产出 `/app/output/result.json`：

```json
{
  "case_id": "...",
  "<output_contract 的每个键>": "<实际值>",
  "status": "ok | partial | insufficient_evidence | input_error",
  "artifacts": [{"path": "...", "role": "...", "kind": "tex|bib|cls|pdf|png", "editable": true, "compiled_ok": true}],
  "sources": [{"locator": "...", "quote": "...", "claim": "...", "conflict": false}],
  "uncertainty": [{"kind": "gap|conflict|unverified", "detail": "...", "locator": "..."}],
  "pending_human": ["..."],
  "errors": ["..."],
  "degradations": ["..."],
  "change_log": [{"time": "...", "step": "...", "note": "..."}],
  "runtime": {"started_at": "...", "finished_at": "...", "producer": "main.py", "engine": "...", "compile_passes": 3}
}
```

- `output_contract` 的**全部键必须原样回填**：不改名、不删未知键；未完成值给 `null` 或 `[]`，不得省略。保留键（`case_id/status/artifacts/sources/uncertainty/pending_human/errors/degradations/change_log/runtime`）不被 contract 同名字段覆盖。
- `status` 诚实：材料不足=`insufficient_evidence`；部分完成=`partial`；全部满足=`ok`；输入无法解析=`input_error`。
- `sources` 每条含 `locator`（如 `materials/paper.md#L12`）+ 逐字 `quote`；冲突来源都保留并标 `conflict: true`。
- `uncertainty` 记缺口/冲突/待人工确认项；`pending_human` 列必须人工确认的事项；`change_log` 记每步改动时间戳与说明。
- 可编辑主产物（`.tex`）及配套 `.bib`/`.cls` 放入 `/app/output/`；编译成功时随附 PDF 预览。中途异常不得留下不完整 result.json（原子写盘）。

## 4. 执行流程（总预算：900 秒、64 次模型请求、单次 ≤16384 tokens）

**硬性要求：前 3 分钟内必须先落盘一版 result.json 骨架，之后只做增量改进。** 骨架在 P0 的 bootstrap 即写入（t≈0），满足早落盘窗口；每一阶段用 `checkpoint begin/commit` 记账，`plan` 实时查看剩余时间/请求。

| 阶段 | 时间 | 请求预算 | 动作与输入/输出 |
|---|---|---|---|
| P0 bootstrap | 0–60s | ≤3 | `python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work`。写 result.json 骨架 + `/app/work/inventory.json` + `/app/work/state.json`（早落盘）。读 case.json/task.md，确认任务类型。 |
| P1 discover | 60–120s | ≤4 | `python3 scripts/main.py discover`；`python3 scripts/main.py index --out /app/work/source_index.json`。扫描材料、解析 PDF、判断体裁/模板/contract。 |
| P2 draft | 120–240s | ≤16 | `python3 scripts/core_impl.py` 不可直接调用，用 `scripts/main.py` + 生成可编辑 `main.tex`/`.bib`；抽取事实池（数字/单位/引用），回填 result.json 各 contract 字段为初稿值。 |
| P3 compile-fix | 240–660s | ≤30 | `python3 scripts/main.py` 辅助编译（见第 7 节）；循环「编译 → `classify-log` 定位文件:行号与根因 → 修复 → 再编译」；`refcheck` 校验 label/ref/cite 收敛。 |
| P4 verify | 660–840s | ≤8 | `python3 scripts/main.py selfcheck`；逐条核对关键事实与来源（`quote` 取逐字片段），填 `sources`/`uncertainty`/`pending_human`/`change_log`；可编译则附 PDF。 |
| P5 finalize | 840–900s | ≤3 | `python3 scripts/main.py finalize ...` + `selfcheck`；保证 result.json 合法、contract 键齐全。 |

降级触发：`plan` 显示剩余时间 <120s 或剩余请求 <10 时，立即 `finalize` 并诚实标 `partial`/`insufficient_evidence`，列 `pending_human`。脚本调用一律用相对路径 `scripts/main.py`；若 cwd 不是 skill 根目录，回退到 `/skills/weekly-skill/scripts/main.py`。

## 5. 自检清单（逐条勾选，对应评分维度）

**实际效果（40 分）**
- [ ] main.tex 在目标模板下 `-halt-on-error` 至少一次编译通过（或记录首个未被修复的根因：文件:行号）。
- [ ] 每条报错都有 文件:行号 + 分类 + 根因说明，未停留在第一条错误信息（用 `classify-log`）。
- [ ] 公式符号/上下标/正斜体、表格数值与列宽、`\ref`/`\cite` 与原文意图一致。
- [ ] 模板必填项（作者/标题/摘要/关键词/引用样式/文件结构）逐项覆盖。
- [ ] 交付物是可编辑 `.tex`，非 PDF/截图片段；`runtime` 记录 TeX 发行版、宏包、编译命令与轮次。

**使用增益（25 分）**
- [ ] 修改集中在根因处，未做无依据的全局替换；改动有 diff/说明，可回滚。
- [ ] 编译-修复循环有次序：先分类、再定位、后修复、再编译验证；每次编译留 `compile_report`。
- [ ] 相同输入重复运行得到一致产物与状态（确定性，见 `selftest`）。

**创新增益（15 分）**
- [ ] 报错被分类（missing_package/undefined_control_sequence/math_error/undefined_reference/…），而非只报一行。
- [ ] 对报错级联做了根因判定，并给出与分类对应的修复策略。
- [ ] 编译验证闭环：写稿 → 编译 → 解析 log → 修复 → 再编译，直至稳定或明确止损。

**可靠体验（10 分）**
- [ ] 空输入/坏 JSON/坏编码：退出码 0，写 `insufficient_evidence`/`input_error`，不崩溃。
- [ ] 缺宏包/缺字体/模板类缺失/权限不足：识别并写入 `errors`/`degradations`，继续处理其余材料。
- [ ] `scripts/*.py --help` 可执行；result.json 永远是合法 JSON。

**生态贡献（10 分）**
- [ ] SKILL.md 写清用途、适用条件、调用方式、输入输出、异常处理与时间预算。
- [ ] 脚本仅依赖标准库，可迁移；references/ 说明规则、预算与查错方法。
- [ ] 示例输出明确标注为「示例」，不冒充正式案例结果。

## 6. 降级策略（如实标注 limitations，绝不编造）

- 缺 LaTeX 引擎/宏包/字体：compile 记录 `engine_missing` 与缺失项，result.json 写 `degradations` 与 `limitations`，**不得声称编译成功**；仍交付可编辑 `.tex` 与诊断报告。
- 材料不足/冲突：写 `insufficient_evidence` 或 `source_conflict` + `pending_human`，绝不补造数值、DOI、作者或实验结果。
- 无法解析 case.json 的 output_contract：骨架仍含 case_id/status/sources/uncertainty/pending_human/change_log，标记 `input_error` 并说明缺失字段。
- 写盘失败：降级到最小合法 result.json（元数据 + 缺口说明），不让进程崩溃。
- 预算不足：`must_downgrade` 为真时立即止损 finalize，把已完成的 partial 产物与缺口如实写入，不伪装 `ok`。

## 7. 脚本接口（相对路径，仅标准库）

```bash
python3 scripts/main.py bootstrap  --input /input --output /app/output --work /app/work
python3 scripts/main.py discover   --input /input
python3 scripts/main.py index      --input /input --out /app/work/source_index.json
python3 scripts/main.py plan       --work /app/work
python3 scripts/main.py checkpoint begin  --phase draft --requests 0
python3 scripts/main.py checkpoint commit --phase draft --requests 4 --note "..."
python3 scripts/main.py classify-log --log /app/work/main.log --main main.tex
python3 scripts/main.py quote      --file materials/paper.md --start 10 --end 15
python3 scripts/main.py selfcheck  --input /input --output /app/output --work /app/work
python3 scripts/main.py finalize   --input /input --output /app/output --work /app/work [--status partial] [--engine pdflatex] [--passes 3]
python3 scripts/selftest.py
```

编译由 Agent 在 P3 调用系统 LaTeX 引擎（latexmk/tectonic/pdflatex/xelatex/lualatex 中第一个可用者）执行，`classify-log` 解析 `.log`；引擎缺失时如实记录。脚本通过 `python3 scripts/main.py --help` 查看全部参数；示例输出仅出现在 `references/`，不得冒充正式案例结果。

## 8. 最小调用示例（示例，非正式案例结果）

设 `/input/case.json` 为 `{"case_id":"demo-01","output_contract":{"title":"","body":"","references":""}}`，`/input/materials/paper.md` 含一句 `E = 1.5 eV`。一次最小流程：

```bash
python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work
python3 scripts/main.py checkpoint begin --phase draft
# Agent 生成 /app/output/main.tex 与 /app/output/refs.bib，并回填 contract 字段
python3 scripts/main.py checkpoint commit --phase draft --requests 4
python3 scripts/main.py selfcheck --input /input --output /app/output --work /app/work
python3 scripts/main.py finalize --input /input --output /app/output --work /app/work --status partial
```

示意输出（`bootstrap` 打印，仅示例）：

```json
{
  "case_id": "demo-01",
  "status": "partial",
  "contract_keys": ["title", "body", "references"],
  "file_count": 3,
  "early_flush_s": 180
}
```

以上 `demo-01` 与全部数值均为示意，不来自任何正式案例，不冒充测评结果。

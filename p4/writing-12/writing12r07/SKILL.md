---
name: latex-writing-compile-robust
description: 面向 LaTeX 学术写作与编译的可复核、强鲁棒 Skill：读取科研材料与任务约束，生成/修改可编译 .tex 稿件，循环编译并定位、分类、修复错误，校验公式/表格/引用/模板必填项，建立事实-来源映射，产出可编辑产物与 /app/output/result.json。空输入、坏 JSON、坏编码、缺参、超大文件、缺引擎/宏包/字体等异常路径均优雅降级，不崩溃、不编造。当任务要求以 LaTeX 完成写作、改写、模板适配、公式/引用/编译错误修复、可重复构建时使用。
version: 1.0.0
---

# LaTeX 写作与编译 Skill（鲁棒性变体）

## 1. 角色与何时使用

把「科研材料 → 可编译 .tex 稿件 → 可复核结果」沉淀为可重复流程，聚焦题目四问：
**编译得通**（定位到文件:行号与根因，不停在第一条错误）、**改得对**（公式/表格/引用/
模板必填项与原文意图一致）、**交得可用**（交付可编辑 .tex 而非 PDF/截图片段）、
**复现得了**（记录发行版/宏包/字体/命令/轮次）。

适用：依据材料生成或改写论文、迁入指定模板、修复公式/表格/交叉引用/参考文献、
定位编译错误并稳定构建。不适用：纯聊天问答、与 LaTeX 无关的写作。

核心原则：**先编译，再谈对错；事实有出处，缺口显式标注，绝不编造**。数字、单位、
引用逐字复用材料；材料没有的数值补 `MATERIAL GAP` 或写入 `pending_human`。

## 2. 输入契约

运行时输入固定为：
- `/input/case.json`：案例 id、任务说明、材料路径、`output_contract`（须原样回填的字段清单）与证据范围；
- `/input/task.md`：任务与格式约束；
- `/input/` 下任意材料（`materials/`、`*.md`、`*.tex`、`*.bib`、`*.pdf`、图片等）。

**不要假设固定文件名或键名，先扫描发现。** PDF 材料必须用平台 `pdf-text-tool`
（pdftotext）解析实际文件，不得按文件名/领域标签补造内容。工作目录 `/app/work`，
最终产物写 `/app/output/`。

## 3. 输出契约

必须产出 `/app/output/result.json`：

```json
{
  "case_id": "...",
  "<output_contract 的每个键>": "<实际值>",
  "status": "ok | partial | insufficient_evidence | input_error",
  "sources": [],
  "uncertainty": [],
  "pending_human": [],
  "change_log": []
}
```

- `case_id` 取自 case.json（键名可能是 `case_id`/`id`/`challenge_id`/`benchmarkId`/`armiId`，缺失用 `unknown` 并标 `input_error`）。
- `output_contract` 的**全部键必须原样回填**：不改名、不删未知键；未完成值给 `null`/`[]`，不得省略。兼容 dict / list[str] / list[dict] 三种形态。
- `status` 诚实：材料不足=`insufficient_evidence`；部分完成=`partial`；全部满足=`ok`；输入无法解析=`input_error`。
- `sources`：每条含 `locator`（如 `materials/paper.md#L12`）+ 逐字 `quote`；冲突来源都保留并标 `source_conflict`。
- `uncertainty`：缺口、冲突、待人工确认项；`pending_human`：需人裁决的项；`change_log`：每步改动时间戳与说明。
- 可编辑主产物（`.tex`）及配套 `.bib`/`.cls` 放入 `/app/output/`；编译成功时随附 PDF 预览。中途异常不得留下不完整 result.json（脚本用原子写盘保证）。

## 4. 执行流程（总预算：900 秒、64 次模型请求、单次 ≤16384 tokens）

**硬性要求：前 3 分钟内必须先落盘一版 result.json 骨架，之后只做增量改进。**

| 阶段 | 时间 | 请求预算 | 动作与输入/输出 |
|---|---|---|---|
| P0 引导 | 0–60s | ≤3 | `python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work`，先落盘 result.json 骨架 + `/app/work/inventory.json`；再读 case.json/task.md 确认任务类型。 |
| P1 初稿 | 60–180s | ≤15 | 用 `scripts/evidence.py index` 提取材料事实池（数字/单位/引用/图），确定体裁与模板。生成可编辑 `main.tex` + `.bib`，回填 result.json 各 contract 字段为初稿值。 |
| P2 编译-修复 | 180–600s | ≤30 | `python3 scripts/latex_tools.py compile --main … --outdir /app/output` 循环编译；对每条报错先 `classify-log` 定位 文件:行号+类别+根因 再修，不盲改；`refcheck` 校验 label/ref/cite。 |
| P3 核验与溯源 | 600–870s | ≤10 | `python3 scripts/main.py selfcheck`；逐条核对关键事实与来源（`evidence.py quote` 取逐字片段），填 `sources`/`uncertainty`/`change_log`；可编译则附 PDF。 |
| P4 收尾 | 870–900s | ≤6 | `python3 scripts/main.py finalize --input /input --output /app/output --work /app/work` + `selfcheck`；保证 result.json 合法、contract 键齐全。 |

降级触发：若剩余请求 <10 或剩余时间 <120s，立即 `finalize` 并诚实标 `partial`/
`insufficient_evidence`，列 `pending_human`。脚本一律用相对路径 `scripts/evidence.py`；若
cwd 不是 skill 根目录，回退到 `/skills/weekly-skill/scripts/evidence.py`。

## 5. 自检清单（逐条勾选，对应评分维度）

见 `references/checklist.md`，覆盖实际效果（40）、使用增益（25）、创新增益（15）、
可靠体验（10）、生态贡献（10）五维。运行期必跑：

```bash
python3 scripts/main.py selftest     # 鲁棒性回归：空输入/坏 JSON/坏编码/缺参/超大文件/写盘失败/contract 形态
python3 scripts/main.py selfcheck --input /input --output /app/output --work /app/work
python3 scripts/latex_tools.py refcheck --dir /app/output
```

## 6. 降级策略（鲁棒性变体重点）

| 异常 | 处置 |
|---|---|
| 空输入目录 / case.json 缺失 | 退出码 0，写 `insufficient_evidence` 骨架，不抛异常 |
| 坏 JSON | 先整体解析，失败按截断前缀重试；仍失败记 `bad_json`，产出 metadata-only 骨架 |
| 坏编码 | `utf-8-sig`→`utf-8`→`replacement` 三级回退；记 `decoded_with_replacement` 继续 |
| 缺参数/未知参数 | `--input/--output/--work` 缺省指向 `/input`、`/app/output`、`/app/work`；未知参数返回 help 退出码 0 |
| 超大文件 | 单文件 >2MiB 跳过并记 `skipped: exceeds 2MiB`，其余材料继续 |
| 缺 LaTeX 引擎/宏包/字体 | `latex_tools.py compile` 记 `engine_missing`/`missing_package` 等到 `errors`/`degradations`/`limitations`，**不得声称编译成功**；仍交付可编辑 `.tex` 与诊断报告 |
| 材料不足/冲突 | 写 `insufficient_evidence` 或 `source_conflict` + `pending_human`，绝不补造数值、DOI、作者、实验结果 |
| 写盘失败 | 原子写盘（临时文件+rename）失败时降级到最小合法 result.json，进程不崩溃 |
| 重复运行 | 固定引擎顺序、编译命令与轮次、确定性排序与键序，结果一致（时间戳字段除外） |

## 7. 脚本接口（相对路径，仅 Python 标准库）

```bash
python3 scripts/main.py bootstrap   --input /input --output /app/output --work /app/work
python3 scripts/main.py discover    --input /input
python3 scripts/main.py finalize    --input /input --output /app/output --work /app/work
python3 scripts/main.py selfcheck   --input /input --output /app/output --work /app/work
python3 scripts/main.py selftest
python3 scripts/latex_tools.py compile   --main MAIN.tex --outdir /app/output [--passes 3]
python3 scripts/latex_tools.py refcheck  --dir DIR
python3 scripts/latex_tools.py classify-log --log LOG [--main MAIN.tex]
python3 scripts/evidence.py index  --root /input --out /app/work/source_index.json
python3 scripts/evidence.py quote  --file PATH --start N --end M
python3 scripts/evidence.py locate --root /input --pattern 'REGEX' [--limit 20]
```

`--help` 可查看参数。示例输出仅出现在 `references/`，不得冒充正式案例结果。

## 8. 最小调用示例（示例，非正式案例）

```bash
# 1) 引导：立即落盘骨架
python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work
# 2) 建立事实池与来源索引
python3 scripts/evidence.py index --root /input --out /app/work/source_index.json
# 3) 编译-修复循环（每次编译产出 compile_report.json）
python3 scripts/latex_tools.py compile --main /app/output/main.tex --outdir /app/output
python3 scripts/latex_tools.py classify-log --log /app/output/main.log --main /app/output/main.tex
# 4) 收尾自检
python3 scripts/main.py selfcheck --input /input --output /app/output --work /app/work
```

示意输出形如 `{"case_id": "demo", "status": "partial", "contract_keys": [...], ...}`，
仅用于说明接口，不是正式案例结果。

# slides-01 规则与检查清单（references）

本文件是 `SKILL.md` 第 6、7 节的展开细则，供执行 Agent 逐条对照；不与正文重复的
是具体判据与降级矩阵。全部规则以 `/input/case.json` 的 `output_contract` 为准。

## 1. 证据溯源判据（页级金标准）

每页 `content_map`/`evidence_map` 的一条证据必须满足：

| 字段 | 判据 |
|---|---|
| `source_id` | 指向 `/input` 下真实存在的材料（如 `paper.pdf`、`figures/fig2.png`） |
| `locator` | 段落/页码/图号定位，如 `paper.md#p3`、`paper.pdf#page7`、`fig2` |
| `quote` | **逐字命中**材料；不得改写、不得省略数值与单位 |
| `kind` | `figure`/`table`/`data`（实验证据）与 `schematic`（示意图）必须区分 |
| `fidelity` | `verbatim`（逐字）或 `paraphrase`（改写）——claim 中的数字/单位仅允许 `verbatim` |

硬规则：claim 里出现的**数字、单位、方法名、结论表述**，必须能在对应 `quote` 里逐字
找到；找不到就标 `numeric_inference` 并写入 `uncertainty`，绝不静默搬运。示意图
（`schematic`）禁止当作实验数据（`data`）引用。

## 2. 降级矩阵（缺什么标注什么，不编造）

| 场景 | 处置 |
|---|---|
| `/input` 缺失 / case.json 坏 JSON | `status="input_error"`、`case_id="unknown-case"`、`errors` 记录原因，仍写 result.json |
| PDF 加密 / 图表无法提取 / 公式为图片 | 该页 `evidence.locator` 标 `not_extractable`，`uncertainty` 写原因 |
| 模板无可用版式 | `build` 降级为最小合法 deck，`render_record.changes`/`degradations` 记录 |
| 无办公渲染器导出 PDF | 交付 SVG/PNG 预览，`export_pdf=null`，`exceptions`/`degradations` 如实写 |
| 字体缺失 / 页数超限 | `render_record.fonts` 记录实际字体，超限页写 `pending_human` |
| 材料未报告某结论 | 写 `uncertainty`/`pending_human`，绝不补造数值 |

`status` 取值：`ok`（全部完成且证据齐全）、`partial`（有交付但有限制）、
`insufficient_evidence`（材料不足以支撑主线）、`input_error`（输入不可用）。

## 3. 运行前自检命令（本地）

```bash
python3 -B scripts/main.py --help          # 退出码 0
python3 -B scripts/selftest.py             # 全部 PASS，退出码 0
```

本地端到端（不用正式案例，用临时材料验证骨架/构建/校验链路）：

```bash
python3 -B scripts/main.py scaffold --input ./demo/in --work ./demo/work --output ./demo/out
python3 -B scripts/main.py build    --input ./demo/in --work ./demo/work --output ./demo/out
python3 -B scripts/main.py check    --input ./demo/in --work ./demo/work --output ./demo/out
```

`check` 只追加 `compliance_checks` 记录，不改写已交付内容；`build` 在证据校验不通过时
**拒绝生成**（退出码 2），避免把无溯源页面写进 pptx。

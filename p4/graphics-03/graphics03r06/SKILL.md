---
name: weekly-skill
description: >-
  生物机制图与通路图科研绘图 Skill：从科研材料提取实体、关系、数据与图面约束，
  先落盘可复核的中间表示（plan.json 抽取记录 + figure_model.json 规范化模型），
  再确定性生成分层可编辑 SVG 与最小合法 PDF，导出 PNG，产出含 case_id、来源、
  逐字证据、不确定性、pending_human 与修改记录的 result.json，全程可回溯、可降级。
version: 1.1.0
---

# 生物机制图与通路图绘图 Skill

## 1. 角色与何时使用

本 Skill 用于把科研材料（论文段落、图注、数据表、PDF）转成一张可被逐条质疑、
可编辑、可复核的生物机制图/通路图。不做统计分析、实验设计、论文版式美化。

核心目标（对应评分维度）：①实体画对（基因/蛋白/复合物/家族/细胞类型/区室不混用，
同名基因与蛋白分列为不同实体）；②关系画准（激活/抑制/结合/修饰/转运可区分，箭头与
端点样式正确）；③通路方向与区室布局正确；④图例、配色、线型与实际图面自洽；⑤交付
分层可编辑源文件 + 真实 PNG/PDF 导出件；⑥每个关键元素可回溯到材料的逐字片段
（locator + quote）；⑦缺数据/缺软件如实降级，不编造。

**本版设计取舍（中间表示与可复核）**：把"读材料 → 出图"拆成三层递进产物，评审者可
独立复核推理链而不只看到成品图——
`plan.json`（Agent 的原始抽取，含逐字 quote）→ `figure_model.json`（脚本确定性
归一化/校验后的规范模型）→ `mechanism.svg`/`mechanism.pdf`/`result.json`（由模型
唯一派生）。SVG 每个图元带 `data-entity-id`/`data-relation-id`/`data-source-locator`
回指模型与来源。

## 2. 输入契约

- 运行时原始输入：`/input/case.json`（案例元数据与材料引用）+ `/input/task.md` +
  按 topic 提供的科研材料/PDF。
- **必须实际读取材料文件，不得依据文件名/标签臆测内容**。PDF 用平台
  `pdf-text-tool`/`pdf-render-tool` 读取原文；脚本（纯标准库）不能读 PDF，须由 Agent
  把提取文本写入 `/app/work/materials_text/` 并在 locator 中引用真实位置。
- 工作目录：`/app/work`；最终产物与可编辑产物写入 `/app/output/`。
- 预算：总 900 秒、64 次模型请求、单次响应含推理 ≤16384 token；三个案例各独立运行。

## 3. 输出契约

必须产出 `/app/output/result.json`，字段定义见 `references/output_contract.md`，
至少含：

- `case_id`（取自 `/input/case.json`，不得为空）
- `status`：`success / partial / insufficient_evidence / input_error / failed`
- `editable_source_files`：分层可编辑 SVG（命名图层 + data-* 溯源属性）
- `export_artifacts`：真实 SVG/PDF/PNG（含尺寸、分辨率、字体、色彩检查）
- `entities` / `relations` / `legend` / `data_mappings`：结构化机制规格
- `figure_model`：规范化中间模型；`intermediate_artifacts`：plan.json 与 figure_model.json
- `sources`：来源定位（元素 → locator + 逐字 quote）
- `uncertainty` / `pending_human`：歧义、方向不明、无来源数值、需人工确认项
- `change_log`：增量修改记录（首条必须为 3 分钟内骨架落盘）
- `errors` / `degradations` / `limitations`：partial/failed/insufficient 时如实填写
- `runtime`：起止时间

源文件、导出件、figure_model.json、result.json 路径互相对应，且都真实写入 `/app/output/`。

## 4. 执行流程（总 900 秒 / 64 请求）

脚本调用一律用相对 Skill 根目录 `/skills/weekly-skill` 的相对路径。
每次运行主脚本都会：读 plan.json → 归一化/校验 → 确定性渲染 SVG+PDF → 合并
result.json，可安全重复执行（增量、幂等）。

### 阶段 0：0–3 分钟（≤8 请求）——先落盘骨架（不得跳过）
1. 读 `/input/case.json`、`/input/task.md`，定位材料文件；PDF 用 `pdf-text-tool` 读取。
2. 立即运行 `python scripts/main.py --input /input --output /app/output --work /app/work`。
   此时尚无 plan.json，脚本会写入一版合法 partial/insufficient 骨架 result.json 与
   最小 SVG/PDF。此步保证超时/中断时仍有可交付部分结果。

### 阶段 A：3–10 分钟（≤30 请求）——抽取与建模迭代
3. 逐条读材料，抽取实体/关系/数据/区室/图例，写入 `/app/work/plan.json`：
   - `entity_mentions[]`：mention/resolved_id/type_hint/label/confidence/sources[]（locator+quote）
   - `relation_mentions[]`：subject/object/predicate_text/semantic_hint/direction_hint/evidence/sources[]
   - `data_items[]`、`layout_hints[]`（compartment/transmembrane/basis）、`legend_hints[]`、
     `ambiguities[]`、`pending_human[]`
4. 核对实体命名（基因斜体/蛋白正体、物种前缀、同义名归并）、关系语义与方向、
   区室与跨膜位置（规则见 `references/figure_rules.md`）。
5. 每次修改后重跑 `python scripts/main.py ...`，读退出码与 warnings；阻断错误清零。
   首条骨架后只做增量改进，每轮在 change_log 留记录。

### 阶段 B：10–14 分钟（≤20 请求）——导出与自检
6. 用平台 `browser-automation` 把 `mechanism.svg` 光栅化为 PNG（≥2× 缩放），核对尺寸、
   分辨率、字体、色彩；用 `python scripts/main.py --register-export /app/output/mechanism.png
   --format png --width ... --height ... --dpi ... --generated-by browser-automation` 注册。
7. 补全 `sources`（每个实体/边/数值 → locator + quote）与 `uncertainty`。
8. `python scripts/main.py --check --result /app/output/result.json`，逐条修复。

### 阶段 C：14–15 分钟（≤6 请求）——收尾
9. 无法解决的缺口写入 `limitations`，status 按实际取 success/partial/insufficient；
   重跑 `--check` 确认；更新 change_log。预算只是上限，不必用完。

## 5. 自检清单（提交前逐条勾选）

- [ ] 已读 `/input/case.json` + `/input/task.md` + 实际材料文件（非仅文件名）
- [ ] plan.json 每条实体/关系/数据都带 locator + 逐字 quote，无来源不编造
- [ ] 实体命名规范：基因斜体、蛋白正体、物种前缀、同义名归并、复合物/家族不混用
- [ ] 关系语义：activation/inhibition/binding/modification/transport/conversion 正确且可区分
- [ ] 通路方向与区室：受体→胞内→核内、跨膜位置、分支与反馈环无误
- [ ] 图例与实际配色/线宽/形状/箭头自洽（由脚本统一生成）
- [ ] `editable_source_files` 为分层可编辑 SVG；`export_artifacts` 含真实 SVG/PDF/PNG
- [ ] `figure_model.json` 与 `plan.json` 已写入 `/app/output/` 且与 result.json 一致
- [ ] `sources` 覆盖关键实体/边；`uncertainty`/`pending_human` 记录歧义与缺口
- [ ] `change_log` 含骨架落盘首条与后续增量；status 与 limitations 如实
- [ ] `python scripts/selftest.py` 退出码 0；`python scripts/main.py --check` 退出码 0 或仅警告

## 6. 降级策略（禁止编造）

- 材料缺失：无法确定的实体/方向/数值写入 `uncertainty`，图上不画未证实关系，`limitations` 注明。
- 同名基因与蛋白歧义：按上下文证据判定并分列实体；证据不足列 warning 并写 `uncertainty`。
- 导出工具缺失（无 browser-automation）：仍交付 SVG+PDF，PNG 项不写，`limitations` 说明，不伪造。
- 字体缺失：SVG 用可替换 font-family 栈；PDF 内置 Helvetica，CJK 退化为 `?`，font_check 如实记录。
- 时间/请求不足：先保证骨架已落盘，再按 0→A→B→C 顺序增量改进；超时即停止并保留当前 result.json。
- 空输入/坏 JSON：脚本退出码 0，写 `insufficient_evidence`/`input_error` 合法结果，记录 errors。
- 任何情况下不得编造评测分数、伪造运行结果、补造科学数据或假文件路径。

## 脚本与参考

- `scripts/main.py`：入口（build/check/register-export），读 plan.json→模型→渲染→合并 result.json。
- `scripts/figure_model.py`：中间表示层（归一化/校验/确定性布局/SVG/PDF 生成）。
- `scripts/selftest.py`：离线自检（happy path/空输入/坏 JSON/导出注册）。
- `references/output_contract.md`：result.json 全部字段定义。
- `references/figure_rules.md`：实体/关系/布局/图例规则与清单。

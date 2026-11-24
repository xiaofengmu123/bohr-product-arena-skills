---
name: weekly-skill
description: >-
  生物机制图与通路图科研绘图 Skill：从科研材料抽取实体、关系、数据与图面约束，
  生成分层可编辑 SVG 源文件并导出 PNG/PDF，产出可复核 result.json。
  本版为证据溯源强化变体：每个实体/关系/图例项/数据映射都携带 locator 与逐字 quote，
  evidence_map 细粒度独立可复核，缺失处如实降级，不编造。
version: 1.1.0
---

# 生物机制图与通路图绘图 Skill（证据溯源强化版）

## 1. 角色与何时使用

面向"生物机制图与通路图"科研绘图：把论文段落、图注、数据表或 PDF 转成一张可逐条质疑、可编辑、可复核的机制图/通路图。不承担数值统计分析、实验方案设计、论文版式美化。

核心目标（对应评分维度）：①实体画对（基因/蛋白/复合物/家族/细胞类型/区室不混用）；②关系画准（激活/抑制/结合/修饰/转运/转化/募集可区分）；③通路方向与区室布局正确；④交付分层可编辑 SVG + 真实 PNG/PDF；⑤关键元素可回溯到材料；⑥缺数据/缺软件时如实降级。

**本版取舍：证据溯源前置。** 不允许"画完再补来源"：每条结论写入 spec.json 时就携带 `evidence` 数组，每项必含 `locator`（材料文件 + 段落/页码/图号）与逐字 `quote`，再由 `evidence_map` 汇总成细粒度映射（element_ref/kind/locator/quote/claim/confidence）。

## 2. 输入契约

- 运行时原始输入：`/input/case.json`（案例元数据）+ `/input/task.md`（任务说明）+ 按 topic 提供的材料/PDF。
- 必须实际读取材料文件，不得据文件名/标签臆测内容；PDF 用平台 `pdf-text-tool` / `pdf-render-tool`，不假设有预提取答案层。
- 工作目录 `/app/work`；最终产物写 `/app/output/`（含 result.json）。
- 预算：900 秒、64 次模型请求、单次响应含推理 ≤16384 token；三案例各独立运行。脚本调用一律用相对路径 `scripts/build_svg.py`（相对 Skill 根目录 `/skills/weekly-skill`）。

## 3. 输出契约

必须产出 `/app/output/result.json`，字段见 `references/output_contract.md`，至少含：

- `case_id`（取自 `/input/case.json`，多键回退由 `scripts/main.py skeleton` 完成）
- `editable_source_files`：分层可编辑 SVG（`build_svg.py` 输出，命名图层 compartments/relations/entities/labels/legend，含 `data-*` 证据属性）
- `export_artifacts`：真实 PNG/PDF 导出件（尺寸、分辨率、字体、色彩检查；PNG 尺寸由 `check-result` 从 IHDR 回填）
- `entities` / `relations` / `legend` / `data_mappings`：结构化机制规格（与 spec.json 一致，均含 evidence）
- `sources` / `evidence_map` / `uncertainty` / `pending_human`：溯源与缺口
- `limitations` / `degradations` / `errors`：partial/failed 或缺工具时必填
- `change_log`：增量记录，**首条必须为前 3 分钟骨架落盘**
- `status`：`success` / `partial` / `failed` / `insufficient_evidence` / `input_error`

源文件、导出件、result.json 三者路径互相对应且均实际写入 `/app/output/`。中间规格写 `/app/work/spec.json`。

## 4. 执行流程（总 900 秒 / 64 请求）

### 阶段 A：0–3 分钟（≤8 请求）——必须先落盘骨架
1. `python scripts/main.py discover`：扫描 `/input`，写 `/app/work/input_manifest.json`。
2. 读取 `/input/case.json`、`/input/task.md`，定位并读取材料文件（PDF 用解析工具）。
3. 快速抽取实体/关系第一版，写 `/app/work/spec.json`（每条先带 locator+quote 或空 evidence）。
4. `python scripts/main.py validate-spec /app/work/spec.json --report /app/work/spec.validation_report.json`：阻断项（exit 1）修到清零。
5. `python scripts/main.py build-svg /app/work/spec.json --out /app/output/mechanism.svg`。
6. `python scripts/main.py skeleton --case /input/case.json`：落盘合法 partial result.json（status=partial、case_id 已填、全字段、change_log 首条）。**此步不得跳过**，超时/中断仍有可交付部分结果。

### 阶段 B：3–10 分钟（≤30 请求）——抽取与建模迭代
7. 逐条核对实体命名（基因斜体/蛋白正体、物种前缀、同义名归并、复合物与家族不混用），修正 `entities`。
8. 逐条判定关系语义（促进→activation、抑制→inhibition、磷酸化→modification、转运→transport…）与 direction/evidence_strength/modifier。
9. 定区室布局（extracellular→membrane→cytosol→nucleus；跨膜受体 `transmembrane:true`）。
10. **证据回填**：每个实体/关系/数据映射补全 evidence（locator + 逐字 quote）；无原文可引标 `not_reported` 并记 uncertainty。冲突两来源都记并标 `source_conflict`。
11. 每次修改后重跑 `validate-spec`（exit 1 清零）→ `build-svg` 覆盖 SVG，并在 result.json 的 change_log 记一条。

### 阶段 C：10–14 分钟（≤20 请求）——导出与自检
12. 用平台 `browser-automation` / `office-renderer` 把 SVG 渲染为 PNG/PDF（优先 2x 或 300dpi）；用 `font-discovery` 核对字体。无法渲染的格式如实不写，缺的工具记入 `degradations`。
13. 补全 `sources`、`evidence_map`（每关键结论一行）、`uncertainty`、`pending_human`，写回 `export_artifacts` 的检查结果。
14. `python scripts/main.py check-result --result /app/output/result.json --output-dir /app/output`，逐条修复 exit 1 项。

### 阶段 D：14–15 分钟（≤6 请求）——收尾
15. 无法解决的缺口写入 `limitations`；status 按实际取 success/partial/failed；重跑 check-result 至 exit 0 或仅警告；更新 change_log。

## 5. 自检清单（对应评分维度，逐条勾选）

- [ ] 已读 `/input/case.json` + `task.md` + 实际材料文件（非仅文件名）
- [ ] 实体命名规范：基因斜体、蛋白正体、物种前缀、同义名归并、复合物/家族不混用
- [ ] 关系语义：activation/inhibition/binding/modification/transport/conversion/recruitment 正确且可区分
- [ ] 通路方向：受体→胞内→核内、跨膜位置、分支与反馈环无误
- [ ] 图例与实际配色/线宽/形状/箭头自洽（由 `build_svg.py` 同源生成）
- [ ] 数据编码均有 evidence（locator+quote）；无来源不标注
- [ ] `editable_source_files` 为分层可编辑 SVG；`export_artifacts` 含真实 PNG/PDF（魔数通过）
- [ ] `evidence_map` 覆盖关键实体/边/图例项/数据映射，每项含 element_ref+locator+逐字 quote
- [ ] `uncertainty` 记录歧义与缺口；`change_log` 首条为骨架落盘；status/limitations 如实
- [ ] `scripts/main.py selftest` 通过；`check-result` exit 0 或仅警告（警告已说明）

## 6. 降级策略（禁止编造）

- 材料缺失：无法确定的实体/方向/数值写入 `uncertainty`，图上不画未证实关系，`limitations` 注明。
- 同名基因/蛋白歧义：按上下文证据判定；证据不足列警告并记 `gene_protein_ambiguous`。
- 导出工具缺失（无 browser-automation/office-renderer）：仍交付 SVG 源文件，`export_artifacts` 仅保留能真实生成的格式，缺失格式记 `degradations`，不得伪造 PNG/PDF 记录。
- 字体缺失：SVG 用可替换 font-family 栈；导出时字体不可用则如实记录 `font_check` 状态。
- 时间/请求不足：先保证骨架已落盘，再按 A→B→C→D 顺序增量改进；超时即停并保留当前 result.json。
- 任何情况下不得编造评测分数、伪造运行结果、补造科学数据或假文件路径；`runtime` 中的模型/版本/分数由平台记录，仅如实引用。

## 脚本与参考

- `scripts/main.py`：discover / skeleton / validate-spec / build-svg / check-result / selftest 六子命令，exit 0=干净、1=阻断、2=仅警告。
- `scripts/build_svg.py`：由 spec.json 生成分层 SVG（命名图层 + 语义箭头 + 区室布局 + 自洽图例 + `data-*` 证据属性），纯标准库、确定性。
- `scripts/selftest.py`：包级自检（frontmatter、模块导入、SVG 可解析、skeleton 合法、validate/check 能抓错）。
- `references/output_contract.md`：result.json / spec.json 全部字段定义与完整性梯度。
- `references/entity_relation_rules.md`：实体/关系/布局/图例规则清单（供抽取阶段逐条对照）。

## 最小调用示例（示意，非正式案例）

```text
python scripts/main.py discover
# Agent 读材料 → 写 /app/work/spec.json（含 evidence）
python scripts/main.py validate-spec /app/work/spec.json --report /app/work/spec.validation_report.json
python scripts/main.py build-svg /app/work/spec.json --out /app/output/mechanism.svg
python scripts/main.py skeleton --case /input/case.json
# Agent 用 browser-automation/office-renderer 导出 PNG/PDF 并回填 result.json
python scripts/main.py check-result --result /app/output/result.json --output-dir /app/output
```

提交前运行 `python scripts/main.py selftest`；结果以正式案例实际运行与官方 Harness 为准，示例仅示意接口。

---
name: weekly-skill
description: >-
  生物机制图与通路图科研绘图 Skill：从科研材料抽取实体、关系、数据与图面约束，
  生成分层可编辑 SVG 源文件并导出 PNG/PDF，产出可复核的 result.json（case_id、
  源文件、导出件、来源、不确定性、pending_human、修改记录），全程证据可溯源、
  证据分级（direct/inferred/missing）、可降级，缺数据不编造。
version: 1.1.0
---

# 生物机制图与通路图绘图 Skill

## 1. 角色与何时使用

本 Skill 用于把科研材料（论文段落、图注、数据表、PDF）转成一张可被逐条质疑、可编辑、可复核的生物机制图/通路图。适用：机制图、通路图、示意图、流程图、图表编辑。不适用：数值统计与统计推断、实验方案设计、论文版式美化。

核心目标（对应评分维度）：①实体画对（基因/蛋白/复合物/家族/细胞类型/区室不混用）；②关系画准（activation/inhibition/binding/modification/transport/conversion 可区分）；③通路方向与数据编码正确；④交付分层可编辑源文件 + 真实 PNG/PDF 导出件；⑤每个关键元素可回溯到材料（locator + 逐字 quote）；⑥缺数据/缺软件时如实降级，不编造。

## 2. 输入契约

- 运行时原始输入：`/input/case.json`（案例元数据与材料引用）+ `/input/task.md`（任务说明）+ 按 topic 提供的科研材料/PDF。
- 必须实际读取材料文件内容，不得依据文件名/标签臆测内容；PDF 用平台 `pdf-text-tool` / `pdf-render-tool` / `ocr-tool` 读取，不要假设存在预提取答案层。
- 工作目录 `/app/work`；最终产物写入 `/app/output/`（含 result.json）。
- 预算：900 秒、64 次模型请求、单次响应含推理 ≤16384 token；三个案例各自独立运行。

## 3. 输出契约

必须产出 `/app/output/result.json`，字段定义见 `references/output_contract.md`，至少包含：

- `case_id`：取自 `/input/case.json`。
- `status`：success / partial / failed / insufficient_evidence / input_error。
- `editable_source_files`：分层可编辑 SVG（由 `scripts/build_svg.py` 生成，含命名图层）。
- `export_artifacts`：真实生成的 PNG/PDF（含尺寸、分辨率、字体、色彩检查），不伪造。
- `entities` / `relations` / `legend` / `data_mappings`：结构化机制规格（与 `/app/work/spec.json` 一致，逐项带 evidence）。
- `sources`：元素 → 材料位置/版本聚合。
- `uncertainty`：歧义实体、方向不明、无来源数值；每条含 `issue`/`basis`/`status`。
- `pending_human`：需要人工裁决的问题（含 `question`/`affects`/`suggested_action`）。
- `change_log`：增量修改记录；首条必须为"骨架落盘"。
- `limitations` / `degradations` / `errors`：partial/failed 必填 limitations；degradations 逐项记录能力降级。
- `runtime`：起止时间、耗时、已用请求数（如实）。

源文件、导出件、result.json 三者路径互相对应，且都实际写入 `/app/output/`。

## 4. 执行流程（总 900 秒 / 64 请求）

### 阶段 A：0–3 分钟（≤180 秒，≤8 请求）——必须先落盘骨架

1. 读 `/input/case.json`、`/input/task.md`，定位并读取材料文件（PDF 用解析工具）。
2. 快速抽取实体/关系第一版，写入 `/app/work/spec.json`。
3. `python3 scripts/main.py build --spec /app/work/spec.json --out /app/output`（生成 mechanism.svg）。
4. `python3 scripts/main.py scaffold --case /input/case.json --out /app/output --work /app/work`，立即得到 status=partial、case_id 已填、字段齐全、change_log 首条"骨架落盘"的 result.json。**此步不得跳过**，保证超时/中断时仍有可交付部分结果。

### 阶段 B：3–10 分钟（≤30 请求）——抽取与建模迭代

5. 逐条核对实体命名（基因斜体/蛋白正体、物种前缀、同义名归并、复合物/家族不混用），修正 entities。
6. 逐条判定关系语义（促进→activation、抑制→inhibition、磷酸化→modification、转运→transport…），修正 relations 的 type/direction/evidence/modifier。
7. 确定区室布局（extracellular→membrane→cytosol→nucleus；跨膜受体 `transmembrane:true`）。
8. 每次改 spec.json 后重跑 `python3 scripts/main.py build ...` 覆盖 SVG，并在 change_log 记录增量。

### 阶段 C：10–14 分钟（≤20 请求）——导出与自检

9. 用 `office-renderer` / `browser-automation` / `latex-compiler` 将 SVG 渲染为 PNG/PDF，核对尺寸、分辨率、字体、色彩，写回 export_artifacts。
10. 补全 sources、uncertainty、pending_human。
11. `python3 scripts/main.py check --result /app/output/result.json --out /app/output`，逐条修复。

### 阶段 D：14–15 分钟（≤6 请求）——收尾

12. 无法解决的缺口写入 limitations/degradations，status 按实际取值；重跑 check 确认；更新 change_log。

脚本调用一律用相对 Skill 根目录 `/skills/weekly-skill` 的相对路径（`scripts/build_svg.py`）；模型请求预算只作上限，不必用完。

## 5. 自检清单（对应评分维度，逐条勾选）

- [ ] 已读 `/input/case.json` + `task.md` + 实际材料内容（非仅文件名）
- [ ] 实体命名规范：基因斜体、蛋白正体、物种前缀、同义名归并、复合物/家族不混用
- [ ] 关系语义：activation/inhibition/binding/modification/transport/conversion 正确且可区分
- [ ] 通路方向：受体→胞内→核内、跨膜位置、分支与反馈环无误
- [ ] 图例与实际配色/线宽/形状/箭头自洽（由 build_svg.py 统一生成）
- [ ] 数据编码（表达量/显著性/置信度）均有 source；无来源不标注数值
- [ ] 每个显示元素有 evidence（locator + 逐字 quote）；missing 元素只画灰虚线并标注
- [ ] `editable_source_files` 为分层可编辑 SVG；`export_artifacts` 含真实 PNG/PDF
- [ ] `sources` 覆盖关键实体/边；`uncertainty` 每条含 basis/status；`pending_human` 列出待决项
- [ ] `change_log` 含骨架落盘 + 后续增量；status/limitations/degradations 如实
- [ ] `python3 scripts/selftest.py` 退出码 0；`scripts/main.py check` 退出码 0 或仅警告（警告已说明）

## 6. 降级策略与不确定性（本版核心：禁止编造）

- 证据分三级：`direct`（材料逐字匹配）、`inferred`（上下文推断）、`missing`（未报道）。只有 direct/inferred 可作为实线肯定结论；missing 不得作为肯定论断。
- 无来源（quote 缺失）的实体/关系/数值不得画入正图：标 `evidence_status="missing"`，图上灰虚线 + 问号，或只入 uncertainty/pending_human，不伪造来源。
- 方向不明、同名基因与蛋白歧义、输入数据与图示矛盾：写入 uncertainty（含 basis 与 status=unresolved/deferred），并在 pending_human 给出待决问题。
- 导出工具缺失（无 office-renderer/browser-automation）：仍交付 SVG 源文件，export_artifacts 仅保留能真实生成的格式，缺失格式写入 limitations/degradations，不得伪造 PNG/PDF 记录。
- 字体缺失：SVG 用可替换 font-family 栈；导出时如实记录 font_check 状态。
- 时间/请求不足：先保证骨架已落盘，再按 A→B→C→D 顺序增量改进；超时即停并保留当前 result.json。
- 任何情况下不得编造评测分数、伪造运行结果、补造科学数据或假文件路径。

## 7. 脚本与参考

- `scripts/main.py`：`scaffold`（落盘 partial 骨架）/ `build`（spec→SVG）/ `check`（校验 result.json 契约与文件存在性、证据完整性）。
- `scripts/build_svg.py`：由 spec.json 生成分层 SVG（命名图层 + 语义箭头 + 区室布局 + 自洽图例 + data-* 溯源属性 + missing 灰虚线降级）。
- `scripts/selftest.py`：包自检（SKILL.md 元数据、脚本可运行、契约 schema、合成示例端到端、确定性比对）。
- `references/output_contract.md`：result.json 全部字段定义、允许取值与证据分级规则。
- `references/entity_relation_rules.md`：实体/关系/布局/图例规则、证据分级与降级清单。

## 8. 最小调用示例（示意接口，非正式案例）

```bash
# 1) 抽取后落盘规格（spec.json 由 Agent 依据材料填写）
python3 scripts/main.py build --spec /app/work/spec.json --out /app/output
# 2) 立即落盘 partial 骨架（前 3 分钟内）
python3 scripts/main.py scaffold --case /input/case.json --out /app/output --work /app/work
# 3) 校验与自检
python3 scripts/main.py check --result /app/output/result.json --out /app/output
python3 scripts/selftest.py
```

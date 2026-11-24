---
name: weekly-skill
description: >-
  生物机制图与通路图科研绘图 Skill：从科研材料抽取实体、关系、数据与图面约束，
  以单一中间表示（MIR）驱动确定性布局与分层 SVG 渲染，导出 PNG/PDF，产出可复核的
  result.json（case_id、主产物、entities/relations/legend/data_mappings、sources、
  uncertainty、pending_human、change_log、limitations），全程证据溯源、可降级。
version: 2.0.0
---

# 生物机制图与通路图科研绘图 Skill

## 1. 角色与何时使用

把科研材料（论文段落、图注、数据表、PDF）转成一张**可被逐条质疑、可编辑、可复核**的机制图/通路图。当任务要求“读材料 → 定实体与关系 → 定布局与图例 → 出可编辑源文件与导出件”时使用。不做统计分析、实验设计、论文版式美化。

判定做对的唯一标准：图上的每个实体与每条边都能回溯到材料或权威命名；图例、颜色映射与图面自洽；他人能打开源文件改一个标签并重新导出。图是机制陈述，箭头与定位都带语义，画错一处即给出错误论断。

核心目标（对应评分维度）：①实体画对（基因/蛋白/复合物/家族/细胞类型/区室不混用）；②关系画准（激活/抑制/结合/修饰/转运/转化可区分）；③通路方向与数据编码正确；④交付分层可编辑源文件 + 真实 PNG/PDF；⑤每项关键结论带 locator + 逐字 quote；⑥缺数据/缺软件如实降级，不编造。

## 2. 输入契约

- 运行时原始输入：`/input/case.json`（案例元数据与材料引用）+ `/input/task.md`（任务说明）+ 按 topic 提供的科研材料/PDF。
- 必须实际读取材料文件，禁止凭文件名/标签臆测内容。PDF 用平台 `pdf-text-tool` / `pdf-render-tool` 读取，不要假设有预提取答案层。
- 工作目录 `/app/work`；最终产物写入 `/app/output/`（含 `result.json`）。
- 预算：900 秒、64 次模型请求、单次响应含推理 ≤16384 token；三个案例各独立运行。

## 3. 输出契约

必须产出 `/app/output/result.json`，字段定义见 `references/output_contract.md`，至少包含：

- `case_id`（取自 `/input/case.json`，取不到再用 `task.md` 的 challenge_id，最后 `unknown`）
- `editable_source_files`：分层可编辑 SVG（由 `scripts/main.py render` 生成，含命名图层与 `data-*` 溯源属性）
- `export_artifacts`：真实 PNG/PDF 导出件（含尺寸、分辨率、字体、色彩检查结果；未真实生成不得记录）
- `entities` / `relations` / `legend` / `data_mappings`：结构化机制规格（与 `spec.json` 一致，每项带 `sources`）
- `sources`：聚合溯源（元素 → locator + 逐字 quote + 材料文件）
- `uncertainty`：歧义实体、方向不明、无来源数值；`pending_human`：需人工确认项
- `change_log`：增量修改记录（首条必须为 3 分钟内骨架落盘）
- `status`：success / partial / failed；`limitations` / `errors` / `degradations`：如实说明缺口
- `runtime`：起止时间与耗时（请求数由平台记录，勿自报分数）

源文件、导出件、result.json 三者路径互相对应且都实际写入 `/app/output/`。任何非成功状态都必须写清限制，不得补造科学数据或伪造文件路径。

## 4. 执行流程（总 900 秒 / 64 请求）

### 阶段 A：0–3 分钟（≤180 秒，≤8 请求）——必须先落盘骨架

1. 读 `/input/case.json`、`/input/task.md`，用 `scripts/main.py skeleton` 发现 case_id 并落盘 result.json 骨架与空 MIR。
2. 定位并读取材料文件（PDF 用解析工具），快速抽取实体/关系第一版写入 `/app/work/spec.json`。
3. `python scripts/main.py validate --mir /app/work/spec.json`，阻断错误清零。
4. `python scripts/main.py render --mir /app/work/spec.json --out /app/output/mechanism.svg`。
5. `python scripts/main.py merge ...` 把骨架升级为一版 partial 结果。**此步不得跳过**：超时/中断时仍有可交付部分结果。

### 阶段 B：3–10 分钟（≤30 请求）——抽取与建模迭代

6. 逐条核对实体命名（基因斜体/蛋白正体、物种前缀、同义名归并、复合物与家族不混用），修正 `entities`。
7. 逐条判定关系语义（促进→activation、抑制→inhibition、磷酸化→modification+modifier、转运→transport、结合→binding…），修正 `relations` 的 type/direction/evidence/modifier。
8. 确定区室布局（extracellular→membrane→cytosol→nucleus，跨膜受体 `transmembrane:true` 置于膜带）。
9. 每次修改后重跑 validate（阻断清零）+ render（覆盖 SVG），并在 change_log 记录。

### 阶段 C：10–14 分钟（≤20 请求）——导出与自检

10. 用平台导出工具把 SVG 渲染为 PNG/PDF（office-renderer / browser-automation / latex-compiler，视可用工具），核对尺寸、分辨率、字体、色彩，写回 `export_artifacts`；导出元数据写到 `/app/work/exports.meta.json`。
11. 补全 `sources`（每个实体/边 → locator + quote）与 `uncertainty`。
12. `python scripts/main.py check --result /app/output/result.json --output-dir /app/output`，逐条修复。

### 阶段 D：14–15 分钟（≤6 请求）——收尾

13. 无法解决的缺口写入 `limitations`，status 按实际取 success/partial/failed；重跑 check 确认；更新 change_log；运行 `python scripts/selftest.py` 确认包级闭环仍通过。

脚本一律在 Skill 根目录 `/skills/weekly-skill` 下用相对路径 `scripts/main.py` 调用（cwd 未知时用绝对路径 `/skills/weekly-skill/scripts/main.py`）。请求预算只作上限，不必用完。

## 5. 自检清单（对应评分维度，逐条勾选）

- [ ] 已读 `/input/case.json` + `task.md` + 实际材料文件（非仅文件名）
- [ ] 实体命名规范：基因斜体、蛋白正体、物种前缀、同义名归并、复合物/家族不混用
- [ ] 关系语义：activation/inhibition/binding/modification/transport/conversion 均正确且可区分
- [ ] 通路方向：受体→胞内→核内、跨膜位置、分支与反馈环无误
- [ ] 图例与实际配色/线宽/形状/箭头自洽（由同一常量表生成，不手写漂移）
- [ ] 数据编码（表达量/显著性/置信度）均有 `source`，无来源不标注
- [ ] `editable_source_files` 为分层可编辑 SVG；`export_artifacts` 含真实 PNG/PDF
- [ ] `sources` 每条含 locator + 逐字 quote；`uncertainty` 记录歧义与缺口
- [ ] `change_log` 首条为骨架落盘；status 与 limitations 如实
- [ ] `scripts/selftest.py` 退出码 0；`check` 退出码 0 或仅警告（警告已说明）

## 6. 降级策略（禁止编造）

- 材料缺失：无法确定的实体/方向/数值写入 `uncertainty`，图上不画未证实关系，`limitations` 注明。
- 同名基因与蛋白歧义：按上下文证据判定；证据不足时列为警告并在 uncertainty 说明。
- 导出工具缺失（无 office-renderer/browser-automation）：仍交付 SVG 源文件，`export_artifacts` 仅保留能真实生成的格式，缺失格式在 limitations 说明，不得伪造 PNG/PDF 记录。
- 字体缺失：SVG 用可替换 font-family 栈；导出时字体不可用则如实记录 font_check 状态。
- 时间/请求不足：先保证骨架落盘，再按 A→B→C→D 增量改进；超时即停止并保留当前 result.json。
- 任何情况下不得编造评测分数、伪造运行结果、补造科学数据或假文件路径。

## 7. 证据溯源（科研场景硬要求）

每个关键结论/数值/图元都必须指回材料中的**逐字片段**：`{"locator": "materials/paper.md#L12", "quote": "原文逐字句", "material": "paper.md"}`。冲突信息不隐藏：同一指标两个来源不一致时两条都记录并标 `source_conflict`。不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。

## 脚本与参考

- `scripts/main.py`：入口（skeleton/validate/render/merge/check/pipeline/selftest），<30KB。
- `scripts/core_impl.py`：MIR 常量表 + 归一化 + 校验 + 确定性布局 + 分层 SVG 渲染 + 契约自检。
- `scripts/selftest.py`：包级自检（合成样例、空输入、坏 JSON、确定性、图例自洽、契约识别）。
- `references/output_contract.md`：result.json 全部字段定义。
- `references/mechanism_rules.md`：实体/关系/布局/图例/命名规则与对照清单。

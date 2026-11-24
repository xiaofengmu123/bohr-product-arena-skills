---
name: weekly-skill
description: >-
  生物机制图与通路图科研绘图 Skill：从科研材料提取实体、关系、数据与图面约束，
  以 spec.json 为唯一科学模型与渲染模型，自动生成分层可编辑 SVG（命名图层、
  语义箭头、区室布局、自洽图例、data-* 溯源属性），并交付可复核的 result.json
  （含 case_id、全部 output_contract 字段、sources、uncertainty、pending_human、
  change_log），全程可回溯、可降级、可增量恢复。
version: 1.0.0
---

# 生物机制图与通路图科研绘图 Skill

## 1. 角色与何时使用

本 Skill 面向“生物机制图与通路图”科研绘图任务：把科研材料（论文段落、图注、数据表、PDF）
转成一张可被逐条质疑、可编辑、可复核的机制图/通路图。不做统计分析、实验设计、论文版式美化。

核心目标（对应评分维度）：①实体画对（基因/蛋白/复合物/家族/细胞类型/区室不混用）；
②关系画准（激活/抑制/结合/修饰/转运可区分）；③通路方向与数据编码正确；④交付分层可编辑
源文件 + 真实 PNG/PDF 导出件；⑤每个关键元素可回溯到原始材料（locator + 逐字 quote）；
⑥缺数据/缺软件时如实降级，不编造。详细规则见 `references/entity_relation_rules.md`。

## 2. 输入契约

- 运行时原始输入：`/input/case.json`（案例元数据与材料引用）+ `/input/task.md`（任务说明）
  + 按 topic 提供的科研材料/PDF。
- **必须实际读取材料文件**，不得依据文件名/标签臆测内容；PDF 用平台
  `pdf-text-tool` / `pdf-render-tool` 读取，不要假设有预提取答案层。
- 工作目录 `/app/work`；最终产物写入 `/app/output/`（含 `result.json`）。
- 预算：**900 秒、64 次模型请求、单次响应含推理 ≤16384 token**；三个案例各独立运行。

## 3. 输出契约

必须产出 `/app/output/result.json`，字段见 `references/output_contract.md`，至少包含：

- `case_id`（取自 `/input/case.json`）
- `editable_source_files`：分层可编辑源文件（SVG，`scripts/main.py build` 输出，含命名图层）
- `export_artifacts`：PNG/PDF 导出件（含尺寸、分辨率、字体、色彩检查）
- `entities` / `relations` / `legend` / `data_mappings`：结构化机制规格（与 spec.json 一致，含 sources）
- `sources`：聚合来源定位（元素 → 材料位置/版本）
- `uncertainty`：歧义实体、方向不明、无来源数值
- `pending_human`：需人工确认项
- `change_log`：增量修改记录（**首条必须为 3 分钟内 `action="skeleton"` 骨架落盘**）
- `status`：success / partial / failed；`limitations`：partial/failed 时必填非空
- 其余必填键：`title`、`task_summary`、`errors`、`degradations`、`runtime`

源文件、导出件、result.json 三者路径互相对应，且都实际写入 `/app/output/`。

## 4. 执行流程（总 900 秒 / 64 请求）

### 阶段 A：0–3 分钟（≤180 秒，≤8 请求）——先落盘骨架，不得跳过
1. 读 `/input/case.json`、`/input/task.md`，定位并读取材料文件（PDF 用解析工具）。
2. 立即执行 `python scripts/main.py init --case /input/case.json --task /input/task.md --out /app/output --work /app/work`
   ——生成合法 partial `result.json`（18 字段齐全、change_log 首条 skeleton）与空 `spec.json`。
3. 快速抽取实体/关系第一版写入 `/app/work/spec.json`（覆盖内容，保留结构）。
4. 立即 `python scripts/main.py build --spec /app/work/spec.json --out /app/output` 生成 SVG。
5. 确认 `/app/output/result.json` 与 `mechanism.svg` 已存在。**此步保证超时/中断时仍有可交付部分结果。**

### 阶段 B：3–10 分钟（≤30 请求）——抽取与建模迭代
6. 逐条核对实体命名（基因斜体/蛋白正体、物种前缀、同义名归并），修正 `spec.json` 的 `entities`。
7. 逐条判定关系语义（促进→activation、抑制→inhibition、磷酸化→modification、转运→transport…），
   修正 `relations` 的 type/direction/evidence/modifier。
8. 确定区室布局（extracellular→membrane→cytosol→nucleus，跨膜受体 `transmembrane:true`）。
9. 每次修改后重跑 `build`（自动校验 spec，阻断项报错退出），再把 `spec.json` 的
   entities/relations/legend/data_mappings/uncertainty 同步进 `result.json`，并追加 change_log。

### 阶段 C：10–14 分钟（≤20 请求）——导出与自检
10. 用平台导出工具把 SVG 渲染为 PNG/PDF（office-renderer / browser-automation / latex-compiler，
    视可用工具），核对尺寸、分辨率、字体、色彩，写回 `export_artifacts`。
11. 补全 `sources`（每个实体/边 → 材料位置 + 逐字 quote）、`uncertainty`、`pending_human`。
12. `python scripts/main.py check --result /app/output/result.json --out /app/output`，逐条修复。

### 阶段 D：14–15 分钟（≤6 请求）——收尾
13. 无法解决的缺口写入 `limitations`；status 按实际取 success/partial/failed；重跑 check 确认；
    更新 change_log（记录最终状态）。

脚本调用一律用相对 Skill 根目录的相对路径（`scripts/main.py …`）；请求预算只作上限，不必用完。

## 5. 自检清单（对应评分维度，逐条勾选）

- [ ] 已读 `/input/case.json` + `task.md` + 实际材料文件（非仅文件名）
- [ ] 实体命名规范：基因斜体、蛋白正体、物种前缀、同义名归并、复合物/家族不混用
- [ ] 关系语义：activation/inhibition/binding/modification/transport/conversion/recruitment 正确且可区分
- [ ] 通路方向：受体→胞内→核内、跨膜位置、分支与反馈环无误
- [ ] 图例与实际配色/线宽/形状/箭头自洽（由 `build` 统一推导生成）
- [ ] 数据编码（表达量/显著性/置信度）均有 `source` 与逐字 quote，无来源不标注
- [ ] `editable_source_files` 为分层可编辑 SVG；`export_artifacts` 含真实 PNG/PDF
- [ ] `sources` 覆盖关键实体/边；`uncertainty`/`pending_human` 记录歧义与缺口
- [ ] `change_log` 首条为 skeleton 且后续增量；status 与 limitations 如实
- [ ] `scripts/main.py check` 退出码 0 或仅 2（警告已说明）；`scripts/selftest.py` 退出码 0

## 6. 降级策略（禁止编造）

- **材料缺失**：无法确定的实体/方向/数值写入 `uncertainty`，图上不画未证实关系，`limitations` 注明。
- **同名基因与蛋白歧义**：按上下文证据判定；证据不足时列为警告并在 uncertainty 说明。
- **导出工具缺失**：仍交付 SVG 源文件，`export_artifacts` 仅保留能真实生成的格式，缺失格式在
  limitations 说明，**不得伪造 PNG/PDF 记录**。
- **字体缺失**：SVG 使用可替换 font-family 栈（Arial→Noto Sans→DejaVu Sans）；导出时字体不可用
  则如实记录 font_check 状态。
- **时间/请求不足**：先保证阶段 A 骨架已落盘，再按 A→B→C→D 顺序增量改进；超时即停止并保留当前 result.json。
- **任何情况**不得编造评测分数、伪造运行结果、补造科学数据或假文件路径。

## 脚本与参考

- `scripts/main.py`：入口。子命令 `init`（骨架落盘）/ `build`（校验+渲染分层 SVG）/
  `check`（校验 result.json 契约与文件存在性，exit 1=阻断、2=警告）/ `selftest`（端到端自检）。
- `scripts/core_impl.py`：实现（spec 归一化、布局引擎、SVG 生成、契约校验、自检），标准库 only。
- `scripts/selftest.py`：自检薄封装，`python scripts/selftest.py`。
- `references/output_contract.md`：result.json 全部 18 字段定义与一致性不变量。
- `references/entity_relation_rules.md`：实体/关系/布局/图例规则与自检清单。

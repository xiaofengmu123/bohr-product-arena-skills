---
name: weekly-skill
description: >-
  生物机制图与通路图科研绘图 Skill：从科研材料提取实体、关系、数据与图面约束，
  生成分层可编辑 SVG 并导出真实 PNG/PDF，产出可复核的 result.json（case_id、
  源文件、导出件、逐字溯源 sources、uncertainty、append-only change_log）。
  执行按 900 秒 / 64 次请求分阶段预算，前 3 分钟先落盘 partial 结果骨架，
  之后只做增量改进；全程可回溯、可降级、不编造。
version: 1.0.0
---

# 生物机制图与通路图绘图 Skill

## 1. 角色与何时使用

本 Skill 面向"生物机制图与通路图"科研绘图：把论文段落、图注、数据表、PDF 等科研材料
转成一张**可被逐条质疑、可编辑、可复核**的机制图/通路图。当需要生成示意图、机制图、
流程图或图表编辑时使用。不做统计分析、实验设计、论文版式美化。

核心目标（对应评分维度）：①实体画对（基因/蛋白/复合物/家族/细胞类型/区室不混用）；
②关系画准（激活/抑制/结合/修饰/转运/转化可区分）；③通路方向与数据编码正确；
④交付分层可编辑源文件 + 真实 PNG/PDF 导出件；⑤每个关键图元可回溯到材料逐字原文；
⑥缺数据/缺软件时如实降级，不编造。

## 2. 输入契约

- 运行时原始输入：`/input/case.json`（案例元数据）+ `/input/task.md`（任务说明）+
  按 topic 提供的科研材料/PDF。
- 必须实际读取材料文件，不得依据文件名/标签臆测内容。PDF 用平台 `pdf-text-tool` /
  `pdf-render-tool` 读取原文，不假设存在预提取答案层。
- 工作目录 `/app/work`；最终产物写入 `/app/output/`。
- 预算：总 **900 秒、64 次模型请求**，单次响应含推理 ≤16384 token；三个案例各自独立运行。

## 3. 输出契约

必须产出 `/app/output/result.json`，字段见 `references/output_contract.md`，至少含：

- `case_id`（取自 `/input/case.json`）
- `editable_source_files`：分层可编辑 SVG（`scripts/build_svg.py` 输出，含命名图层）
- `export_artifacts`：真实 PNG/PDF 导出件（含尺寸、分辨率、字体、色彩检查）
- `entities` / `relations` / `legend` / `data_mappings`：结构化机制规格（与 spec.json 一致）
- `sources`：每个关键元素 → locator + 逐字 quote
- `uncertainty` / `pending_human`：歧义实体、方向不明、无来源数值、待人工确认
- `change_log`：append-only 增量记录（首条必须为 bootstrap 骨架落盘）
- `status`：success / partial / failed / insufficient_evidence / input_error
- `limitations`：status 非 success 时必填

源文件、导出件、result.json 三者路径互相对应，且都真实写入 `/app/output/`。
运行 `scripts/main.py check --output /app/output` 校验契约与文件真实性。

## 4. 执行流程（900 秒 / 64 请求，阶段门控）

**硬性门控：前 3 分钟（≤180 秒、≤8 请求）必须先落盘 partial 骨架。** 骨架由
`scripts/main.py bootstrap` 确定性生成，任何后续异常都会保留一份合法部分结果。

### 阶段 A：0–3 分钟（≤180 秒，≤8 请求）——骨架落盘，不得跳过
1. 读 `/input/case.json` + `/input/task.md`，定位材料文件（PDF 用解析工具）。
2. 立即 `python scripts/main.py bootstrap --input /input --output /app/output --work /app/work`：
   写入 `result.json`（status=partial、case_id 已填、change_log 首条记录骨架落盘）
   与空 `spec.json`。
3. 若时间允许，快速抽取第一版实体/关系写入 `/app/work/spec.json`，随后
   `python scripts/main.py build` 生成 SVG（失败不阻塞，骨架仍在）。

### 阶段 B：3–10 分钟（≤30 请求）——抽取与建模增量
4. 逐条核对实体命名（基因斜体/蛋白正体、物种前缀、同义名归并），修正 `spec.json` 的 `entities`。
5. 逐条判定关系语义（促进→activation、抑制→inhibition、磷酸化→modification、
   转运→transport…），修正 `relations` 的 type/direction/evidence/modifier。
6. 确定区室布局（extracellular→membrane→cytosol→nucleus，跨膜受体 `transmembrane:true`）。
7. 每次修改后：`scripts/main.py validate`（阻断项清零）→ `scripts/main.py build`（覆盖 SVG）
   → `scripts/main.py merge`（增量并入 result.json，append change_log）。

### 阶段 C：10–14 分钟（≤20 请求）——导出与自检
8. 用平台导出工具（office-renderer / browser-automation / latex-compiler）把 SVG 渲染为
   PNG/PDF；核对尺寸、分辨率、字体、色彩，写回 `export_artifacts`。
9. 补全 `sources`（每个实体/边 → locator + quote）与 `uncertainty`。
10. `scripts/main.py check --output /app/output`，逐条修复。

### 阶段 D：14–15 分钟（≤6 请求）——收尾
11. `scripts/main.py finalize --status success|partial|failed --limitation ...` 写 status 与
    limitations；重跑 `check`；最后更新 change_log。

脚本一律用相对 Skill 根目录 `/skills/weekly-skill` 的路径 `scripts/build_svg.py`；模型请求预算
只作上限，不必用完。**任何时刻超时即停止，保留当前 result.json。**

## 5. 科学规则速查

实体/关系/布局/图例的完整判定规则见 `references/entity_relation_rules.md`，要点：
基因斜体、蛋白正体；复合物六边形、家族虚线框、受体跨膜竖线；激活实心箭头、抑制短横杆、
修饰空心箭头、转运双线箭头、结合无箭头虚线、转化双平行线；区室自上而下
extracellular→membrane→cytosol→nucleus；图例由脚本从实际使用的样式自动生成以保证自洽。

## 6. 证据溯源（硬要求）

每个关键实体、每条边、每条数据映射都必须有 `source.locator`（如 `materials/paper.md#p3`）
+ **逐字 quote**（不改写）。冲突来源两处都记录并标 `source_conflict`。材料缺失/无法判断
写入 `uncertainty` + `pending_human`，图上不画未证实关系。脚本在 SVG 中为每个图元嵌入
`data-source-locator` / `data-source-quote` 属性，使溯源直接内嵌于源文件。

## 7. 自检清单（交付前逐条勾选）

- [ ] 已读 `/input/case.json` + `task.md` + 实际材料文件（非仅文件名）
- [ ] 实体命名规范：基因斜体、蛋白正体、物种前缀、同义名归并、复合物/家族不混用
- [ ] 关系语义：activation/inhibition/binding/modification/transport/conversion 均正确可区分
- [ ] 通路方向：受体→胞内→核内、跨膜位置、分支与反馈环无误
- [ ] 图例与实际配色/线宽/形状/箭头自洽（由 build_svg.py 统一生成）
- [ ] 数据编码（表达量/显著性/置信度）均有 source，无来源不标注
- [ ] `editable_source_files` 为分层可编辑 SVG；`export_artifacts` 含真实 PNG/PDF
- [ ] `sources` 覆盖关键实体/边；`uncertainty` 记录歧义与缺口
- [ ] `change_log` 含骨架落盘与后续增量；status 与 limitations 如实
- [ ] `scripts/selftest.py` 退出码 0；`scripts/main.py check` 退出码 0 或仅警告（警告已说明）

## 8. 降级策略（禁止编造）

- 材料缺失：无法确定的实体/方向/数值写入 `uncertainty`，图上不画未证实关系，`limitations` 注明。
- 同名基因与蛋白歧义：按上下文证据判定；证据不足列为 warning 并在 uncertainty 说明。
- 导出工具缺失：仍交付 SVG 源文件；`export_artifacts` 仅保留能真实生成的格式，缺失格式在
  limitations 说明，不得伪造 PNG/PDF 记录。
- 字体缺失：SVG 用可替换 font-family 栈（Arial / Helvetica / sans-serif）；导出时如实记录 font_check。
- 时间/请求不足：骨架已由 bootstrap 保证；按 A→B→C→D 增量改进，超时即停止保留当前 result.json。
- 任何情况下不得编造评测分数、伪造运行结果、补造科学数据或假文件路径。

## 脚本与参考

- `scripts/main.py`：编排器（bootstrap / validate / build / merge / finalize / check / pipeline）。
- `scripts/build_svg.py`：由规格生成分层 SVG（命名图层 + 语义箭头 + 区室布局 + 自洽图例 + data-* 溯源）。
- `scripts/selftest.py`：端到端自检（空输入/坏 JSON/建图/校验/降级）。
- `references/output_contract.md`：result.json 全部字段定义。
- `references/entity_relation_rules.md`：实体/关系/布局/图例规则与清单。

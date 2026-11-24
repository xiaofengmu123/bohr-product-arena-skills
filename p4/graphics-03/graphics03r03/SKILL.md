---
name: weekly-skill
description: >-
  生物机制图与通路图科研绘图 Skill：从科研材料抽取实体、关系、数据与图面约束，
  生成可编辑分层 SVG 源文件并导出 PNG/PDF，产出可复核的 result.json（case_id、
  源文件、导出件、sources、uncertainty、pending_human、change_log）。以确定性
  渲染为核心：同一输入两次运行产物逐字节一致，配 selftest.py 自检与逐条勾选清单。
version: 2.0.0
---

# 生物机制图与通路图绘图 Skill（自检与确定性强化版）

## 1. 角色与何时使用

本 Skill 面向“生物机制图与通路图”科研绘图：把论文段落、图注、数据表或 PDF
转成一张可被逐条质疑、可编辑、可复核的机制图/通路图。不做统计分析、实验设计、
论文版式美化。

核心目标（对应评分维度）：①实体画对（基因/蛋白/复合物/家族/细胞类型/区室不混用）；
②关系画准（激活/抑制/结合/修饰/转运可区分）；③通路方向与数据编码正确；
④交付分层可编辑 SVG + 真实 PNG/PDF 导出件；⑤每个关键元素可回溯到原始材料；
⑥缺数据/缺软件如实降级，不编造。

本版的设计取舍：**确定性优先**。图面不是模型随手画的，而是 `spec.json` 这个单一
事实源经 `scripts/main.py` 纯函数渲染得到；同一输入两次运行产物逐字节一致，
`selftest.py` 负责在交付前证明这一点。模型只写科学内容，图例、坐标、哈希全由
脚本派生，从机制上消灭“图例与图面不一致”。

## 2. 输入契约

- 运行时原始输入：`/input/case.json`（案例元数据与材料引用）+ `/input/task.md`
  + 按 topic 提供的科研材料/PDF。
- **必须实际读取材料文件**，不得依据文件名/标签臆测内容；PDF 用平台
  `pdf-text-tool` / `pdf-render-tool` 读取，不要假设有预提取答案层。
- 工作目录 `/app/work`；最终产物写入 `/app/output/`。
- 预算：900 秒、64 次模型请求、单次响应含推理 ≤16384 token；三个案例各独立运行。
- 脚本默认参数已指向 `/input`、`/app/output`、`/app/work`；本地自检用
  `--case/--task/--out/--work` 覆盖。

## 3. 输出契约

必须产出 `/app/output/result.json`，字段定义见 `references/output_contract.md`，
至少含：`case_id`、`status`、`editable_source_files`、`export_artifacts`、
`entities`、`relations`、`legend`、`data_mappings`、`sources`、`uncertainty`、
`pending_human`、`change_log`；`status=partial/failed/insufficient_evidence` 时
`limitations` 必填。源文件、导出件、result.json 三者路径互相对应且都实际写入
`/app/output/`。每个关键结论给 `locator + quote` 逐字证据（见 references 规则）。

## 4. 执行流程（总 900 秒 / 64 请求，按 A→B→C→D 增量）

### 阶段 A：0–3 分钟（≤180 秒，≤8 请求）——先落盘骨架，不得跳过
1. 读 `/input/case.json`、`/input/task.md`，定位并实际读取材料文件。
2. 立即 `python scripts/main.py skeleton --out /app/output/result.json` 落盘
   `insufficient_evidence` 骨架（保证超时/中断仍有可交付合法结果）。
3. 抽取实体/关系第一版写入 `/app/work/spec.json`（字段见 references）。
4. `python scripts/main.py validate-spec /app/work/spec.json --out /app/work/spec.norm.json`
   —— 阻断错误必须清零，仅剩警告可继续。
5. `python scripts/main.py run --spec /app/work/spec.json --out /app/output --work /app/work`
   生成 `mechanism.svg` 与 `result.json`（status=partial），change_log 首条即骨架落盘。

### 阶段 B：3–10 分钟（≤30 请求）——抽取与建模迭代
6. 逐条核对实体命名（基因斜体/蛋白正体、物种前缀、同义名归并），修正 entities。
7. 逐条判定关系语义（促进→activation、抑制→inhibition、磷酸化→modification、
   转运→transport…），修正 relations 的 type/direction/evidence/modifier。
8. 确定区室布局（extracellular→membrane→cytosol→nucleus，跨膜受体
   `transmembrane:true`）。
9. 每次修改后重跑 `validate-spec`（阻断项清零）与 `run` 覆盖产物，change_log 记增量。

### 阶段 C：10–14 分钟（≤20 请求）——导出与自检
10. 用平台导出工具（browser-automation / office-renderer，缺则跳过）把 SVG 渲染为
    PNG/PDF，核对尺寸、分辨率、字体、色彩，写回 `export_artifacts`。
11. 补全 `sources`（每实体/边 → 材料位置 + 逐字 quote）与 `uncertainty`。
12. `python scripts/main.py check-result /app/output/result.json --output-dir /app/output`
    逐条修复，最终退出码 0 或仅警告。

### 阶段 D：14–15 分钟（≤6 请求）——收尾
13. 无法解决的缺口写入 `limitations`，status 按实际取 success/partial/failed；
    重跑 `check-result`；更新 change_log。

脚本一律用相对路径 `scripts/core_impl.py`（相对 Skill 根 `/skills/weekly-skill`）；
64 请求只是上限，不必用完。

## 5. 自检清单（交付前逐条勾选）

- [ ] 已读 `/input/case.json` + `task.md` + 实际材料文件（非仅文件名）
- [ ] 实体命名规范：基因斜体、蛋白正体、物种前缀、同义名归并、复合物/家族不混用
- [ ] 关系语义：activation/inhibition/binding/modification/transport/conversion 正确可区分
- [ ] 通路方向：受体→胞内→核内、跨膜位置、分支与反馈环无误
- [ ] 图例与实际配色/线宽/形状/箭头自洽（由同一编码表派生）
- [ ] 数据编码（表达量/显著性/置信度）均有 `source`，无来源不标注
- [ ] `editable_source_files` 为分层可编辑 SVG；`export_artifacts` 含真实 PNG/PDF
- [ ] `sources` 覆盖关键实体/边（locator + quote）；`uncertainty` 记录歧义与缺口
- [ ] `change_log` 首条 sec≤180 为骨架落盘；status 与 limitations 如实
- [ ] 交付前运行 `python scripts/selftest.py`，9 项全过（同输入两次产物逐字节一致）

## 6. 降级策略（禁止编造）

- 材料缺失：无法确定的实体/方向/数值写入 `uncertainty`，图上不画未证实关系，limitations 注明。
- 同名基因与蛋白歧义：按上下文证据判定；证据不足列为警告并在 uncertainty 说明。
- 导出工具缺失：仍交付 SVG，`export_artifacts` 仅保留能真实生成的格式，缺失格式
  在 limitations 说明，不得伪造 PNG/PDF 记录。
- 字体缺失：SVG 用可替换 font-family 栈；导出时如实记录 font_check。
- 时间/请求不足：先保证骨架已落盘，再按 A→B→C→D 增量改进；超时即停止保留当前 result.json。
- 任何情况下不编造评测分数、运行结果、科学数据或假文件路径；数值无来源不标注。

## 脚本与参考

- `scripts/main.py`：单一入口。子命令 `skeleton / validate-spec / build-svg /
  check-result / run`；退出码 0=通过、2=仅警告、1=阻断。`run` 是中间产物生产者，
  最终门禁用 `check-result`。
- `scripts/core_impl.py`：纯函数库（归一化、确定性 SVG 渲染、结果组装、契约校验），
  仅标准库；main.py 保持精简。
- `scripts/selftest.py`：离线自检（确定性、契约、空输入/坏 JSON 降级），退出码 0/1。
- `references/output_contract.md`：result.json / spec.json 全部字段定义与退出码。
- `references/entity_relation_rules.md`：实体/关系/布局/图例/降级规则。

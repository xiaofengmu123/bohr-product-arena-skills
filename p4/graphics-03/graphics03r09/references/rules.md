# 实体 / 关系 / 布局 / 图例 规则与自检清单

## 1. 实体命名与消歧

- 基因：斜体（SVG `font-style="italic"`）；蛋白：正体；人源前缀 `h` 或物种缩写按材料标注。
- 同义名/旧名归并到一个实体（`aliases`），不各画一个节点；首选名用材料中的当前命名。
- 复合物（如 `mTORC1`）≠ 家族（如 `MAPK family`）≠ 单个成员（如 `ERK1`），`kind` 不得混用。
- 细胞类型（ellipse）与亚细胞区室（band）分开：区室是定位不是节点。
- 同名基因与蛋白：按上下文判定；证据不足时列入 `uncertainty`，不在图上强加语义。

## 2. 关系语义判定

| 文字线索 | type |
|---|---|
| 促进/激活/上调/诱导 | activation |
| 抑制/阻断/下调/拮抗 | inhibition |
| 磷酸化/泛素化/乙酰化/甲基化 | modification（`modifier` 记修饰类型） |
| 结合/募集/相互作用/形成复合物 | binding |
| 转运/入核/分泌/内吞/跨膜 | transport |
| 转化/裂解/代谢生成 | conversion |

- 方向：材料写"X 激活 Y"→ `from=X, to=Y`；"Y 被 X 激活"同理归一到 X→Y。
- 证据强度：逐字材料为 `stated`，自行推断为 `inferred`；不确定的写 `uncertain:true` 并进 `uncertainty`。

## 3. 通路方向与布局

- 默认 `top_to_bottom`：extracellular → membrane → cytosol → nucleus 自上而下，跨膜受体 `compartment:"membrane"`、`transmembrane:true`，画在膜带内。
- 需要横向时可设 `layout.orientation:"left_to_right"`。
- 分支、并行路径、反馈环都要画：回流边自动走曲线（`build_svg.py` 判定反向并弯曲），不丢不颠倒。
- 布局本身传达方向，不靠无依据的连线。

## 4. 图例与数据编码

- 图例由 `build_svg.py` 按**实际使用**的 relation type 与 entity kind 自动生成，天然与图面自洽；不要手写图例再与图面脱节。
- `data_mappings` 记颜色/线宽/节点大小 → 表达量/显著性/置信度/时间点，每一项要有 `source`。
- 无来源的数值不标注在图上。

## 5. 自检清单（交付前逐条勾选）

- [ ] 已实际读取 `/input/case.json` + `/input/task.md` + 材料文件（PDF 用平台 `pdf-text-tool`/`pdf-render-tool`，不凭文件名臆测）
- [ ] 实体命名规范：基因斜体、蛋白正体、物种前缀、同义名归并、复合物/家族不混用
- [ ] 关系语义 activation/inhibition/binding/modification/transport/conversion 正确且可区分
- [ ] 通路方向受体→胞内→核内、跨膜位置、分支与反馈环无误
- [ ] 图例与实际配色/线宽/形状/箭头自洽（由 build_svg.py 统一生成）
- [ ] 数据编码均有 `source`，无来源不标注
- [ ] `editable_source_files` 为分层可编辑 SVG；`export_artifacts` 含真实 PNG/PDF（或用平台 office-renderer/browser-automation 渲染）
- [ ] `sources` 覆盖关键实体/边，`uncertainty` 记录歧义与缺口
- [ ] `change_log` 首条为 3 分钟内骨架落盘，后续增量
- [ ] `python3 scripts/main.py check result.json --output-dir /app/output` 退出码 0 或仅警告（警告已说明）

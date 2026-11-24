# 实体 / 关系 / 布局 / 图例规则与健壮性清单

本文件是 spec.json 建模与 SVG 生成的规则来源；`build_svg.py` 的查表即实现本文件。修改规则必须同步改表，保证“图例=图面”。

## 1. 实体类型与命名

| type | 图形 | 命名规范 |
|---|---|---|
| `gene` | 椭圆 | 斜体；人类基因大写、鼠基因首字母大写其余小写 |
| `protein` | 圆角矩形 | 正体；人类蛋白大写，鼠蛋白首字母大写 |
| `complex` | 双边框矩形 | 正体，标注亚基（如 `A/B`） |
| `family` | 虚线圆角矩形 | 家族名正体，不混用成员名 |
| `cell_type` | 大圆角矩形 | 细胞类型与分子实体分类型，不混入 |
| `metabolite` | 圆 | 小分子名 |
| `other` | 矩形 | 兜底；须在 `uncertainty` 说明 |

同义名/旧名归并进 `aliases`；实体 `id` 唯一。基因与蛋白同名歧义：按上下文证据判定，证据不足写 `uncertainty`，不猜。

## 2. 关系语义 → 箭头/端点

| type | 端点 | 线型 | 中文触发词示例 |
|---|---|---|---|
| `activation` | 实心箭头 | 实线 | 促进、激活、上调、诱导 |
| `inhibition` | T 形端（bar） | 实线 | 抑制、阻断、下调 |
| `binding` | 无箭头 | 实线 | 结合、招募、形成复合物 |
| `modification` | 实心箭头 + 修饰标注（P/Ub/Me） | 实线 | 磷酸化、泛素化、甲基化 |
| `transport` | 实心箭头 | 虚线 | 转运、入核、分泌、出膜 |
| `conversion` | 空心三角 | 实线 | 转化、代谢为 |

`direction`：`forward`（source→target）、`backward`（箭头指向 source）、`bidirectional`（双箭头）。`evidence` 编码线宽：`high`=2.8、`medium`=2.0、`low`=1.4+虚线；无证据不标 `high`。

## 3. 通路方向与区室布局

区室自上而下：extracellular → membrane（双脂膜线）→ cytosol → nucleus；未知区室落入 `unplaced` 底条并告警。跨膜受体设 `transmembrane:true` 且放 `membrane`，图形贯穿双膜线。箭头方向表达调控流向，反馈环允许箭头上行；画错方向等于错误论断，需逐条复核。

## 4. 图例与数据编码一致性

图例由 `build_svg.legend_entries()` 从同一查表生成：只列出实际出现的实体类型/关系类型/数据映射/区室。数据编码（颜色、线宽、节点大小）必须对应 `data_mappings` 中的 `channel→field→scale`，且每条带 `source`；无来源数值不标注。

## 5. 证据溯源

每个实体/边/数值带 `source: [{locator, quote}]`，quote 为材料逐字片段；SVG 元素写 `data-source-locator` / `data-source-quote`。冲突源都保留并标 `source_conflict`；无来源写 `not_reported` 并进 `uncertainty`/`pending_human`。

## 6. 健壮性清单（本包侧重方向）

| 场景 | 处置 | 结果 |
|---|---|---|
| 空输入目录 | 写 `insufficient_evidence` 骨架 + 占位 SVG | 退出码 0，不抛异常 |
| case.json 坏 JSON / 坏编码 | 记 `errors`，`utf-8-sig`/`latin-1` 容错解码 | 继续处理其余材料 |
| spec.json 缺失/损坏 | 用空骨架，写占位 SVG | 不崩溃 |
| 缺参数 | `--input/--output/--work` 默认 `/input`、`/app/output`、`/app/work` | 正常执行 |
| 单文件 > 2MB | 跳过并记 `degradations` | 不崩溃 |
| 写盘失败 | 降到最小合法产物 / 应急 partial 结果 | 进程不崩溃 |
| 重复运行 | 无随机源，布局确定；同输入两次 SVG 字节一致 | 可复现 |
| 未知区室/类型、悬空关系、重复 id | 跳过 + 告警 | 不崩溃 |

## 7. 提交前自检（与 selftest.py 一致）

```bash
python scripts/main.py --help          # 退出码 0
python scripts/selftest.py             # 全部 PASS
python scripts/main.py check --output /app/output   # 0 或仅警告
```

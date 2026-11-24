# 图表规范（图表选择 / 编码 / 标注 / 可读性 / 发表级导出）

本文是 Skill 执行阶段的判定规则与渲染模板。Agent 在 `plan` 生成 `figure_model.json` 后，
依据本文把每个候选图精化为可投稿图件。每条规则都可被 `check` 或人工逐条核对。

## 1. 图表选择（按数据型 × 目的）

| 数据型 | 目的 | 推荐图型 | 不推荐及原因 |
|---|---|---|---|
| 单连续变量 | 分布 | 直方图 / 核密度 / ECDF | 饼图：无法表达分布 |
| 连续变量 × 无序分类 | 比较 | 分组箱线 / 小提琴 / 点图 | 饼图：面积角度夸大差异；并列柱：样本少时信息浪费 |
| 两个连续变量 | 关系 | 散点（+趋势线/置信带） | 折线：非时间顺序会误导连线 |
| 多个连续变量 | 关系/结构 | 相关热图 / 散点矩阵 | 全部两两并列柱：信息密度低 |
| 时间序列 | 趋势 | 折线（对数轴显式标注） | 并列柱：掩盖时间顺序 |
| 构成（比例） | 构成 | 堆叠/归一化条形 | 3D 饼图 / 多类饼图：角度难以比较 |
| 类别 >6 | 任意 | 聚合为 "其他" 或分面 | 硬塞单图：颜色不可辨 |

**选择依据必须写入 `figure_model.json` 的 `rationale` 与 `rejected`**，不能只写结论。

## 2. 清洗口径（先记录，再处理）

- 缺失值：识别 `""`、`NA/N/A/nan/null/None/-/?` 等记号；统计每列缺失数与比例，写入
  `data_profile.json` 的 `n_missing`；**默认不删除不填充**，仅在任务明确要求时处理并记录规则。
- 异常值：数值列按 IQR 界限 `[Q1−1.5·IQR, Q3+1.5·IQR]` 计数（`n_outliers_iqr`），**只标记不剔除**；
  需要剔除时保留原始值、剔除规则与剔除后样本量。
- 重复记录：统计完全重复行（`n_duplicates`）；去重前必须确认键列，避免误删合法重复。
- 类型混杂：数值列中混入非数值导致 `dtype` 降级为 `text` 时，逐项列出异常字符串，不静默强制转换。
- 单位/量纲：从数据字典读取单位；字典缺失时在 `uncertainty` 记 `pending_human`，图注不得臆造单位。

## 3. 编码方式

- 位置 > 长度 > 颜色/形状：连续量优先映射到 x/y 位置，类别映射到颜色或形状。
- 面积与角度会系统性夸大差异，仅用于构成型图表且必须从 0 开始。
- 无序分类不得映射到连续色标或角度；有序分类可用序数色标但需在图例说明顺序。
- 类别 >6 聚合为 "其他（n）" 或分面，禁止单图塞入 7+ 色相。
- 量纲差异大：分面或标准化（z-score / min-max），图注写明变换；禁止双轴隐瞒量纲差异。
- 对数轴：图例/轴题显式写 `log10`，刻度用对数刻度，数据含 0 或负值时不用对数轴。
- 色标：连续量用单一色相明度渐变或 RdBu 类发散色，避开红绿双色（色觉障碍）。

## 4. 标注与图注

- 轴标题 = 变量名（单位），例：`Cement (kg/m³)`；无单位写 `(arbitrary units)` 或留空并注明。
- 图例项与数据字典一一对应；无数据字典时图例用列名原文，不改写语义。
- 误差线必须写明口径：SD（标准差）/ SEM（标准误）/ 95% CI，并给出 n。
- 显著性：给 p 值（写清检验方法，如 Welch t / Mann–Whitney U / ANOVA）或记号定义（\* p<0.05 等）。
- 图注脱离正文可读：`图 N. 标题。横轴…纵轴…误差线表示均值±SD（n=…）。…`
- 面板编号按 (a)(b)(c) 顺序，图注与面板编号对应。

## 5. 可读性

- 配色：Okabe–Ito 8 色（#E69F00、#56B4E9、#009E73、#F0E442、#0072B2、#D55E00、#CC79A7、#000000）。
- 灰度可辨：导出前用灰度检查，不同类别在灰度下仍有明度差。
- 字号 ≥7pt（最终 100% 缩放），轴题 ≥8pt；单栏宽 3.4 in，双栏 7 in。
- 网格线浅灰、置于数据下方；不默认加背景色。
- 面板数量 ≥6 时分面或拆图，避免单图过挤。

## 6. 发表级导出

- 同时导出：SVG（可编辑）+ PDF（字体嵌入）+ ≥300 dpi PNG。
- 中文：先探测 CJK 字体（fc-list / matplotlib font_manager），缺字体改用英文标签并记录 degradation。
- 尺寸匹配栏宽；PDF `dpi` 与 PNG `dpi=300`、`bbox_inches='tight'`。
- 线宽 ≥0.75pt；marker 尺寸与线宽协调；图例置于数据区外，不遮挡。

## 7. 渲染模板（matplotlib，Agent 按 figure_model 适配后落盘到 work/figures/）

```python
import json, os, sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OKABE = ["#E69F00", "#56B4E9", "#009E73", "#F0E442",
         "#0072B2", "#D55E00", "#CC79A7", "#000000"]
plt.rcParams.update({
    "font.size": 8, "axes.labelsize": 8, "axes.titlesize": 9,
    "xtick.labelsize": 7, "ytick.labelsize": 7,
    "legend.fontsize": 7, "axes.linewidth": 0.75,
    "figure.dpi": 300, "savefig.dpi": 300,
    "svg.fonttype": "none", "pdf.fonttype": 42,
    "axes.grid": True, "grid.alpha": 0.25, "grid.linestyle": "--",
})

def load_table(src):
    # 依据 data_profile.json 的 sources 决定读法；列名逐字匹配，不改写
    if src.endswith((".csv", ".tsv")):
        sep = "\t" if src.endswith(".tsv") else ","
        return np.genfromtxt(os.path.join("/input", src), delimiter=sep,
                             names=True, dtype=None, encoding="utf-8-sig")
    raise NotImplementedError("sqlite 用 sqlite3 读取，见 main.py profile")

def render(fig_model, profile):
    os.makedirs("work/figures", exist_ok=True)
    for f in fig_model["figures"]:
        fig, ax = plt.subplots(figsize=(f.get("export", {}).get("width_in", 3.4),
                                        f.get("export", {}).get("height_in", 3.0)))
        data = load_table(f["source"])
        ct = f["chart_type"]
        # 按 ct 分派：histogram/scatter/heatmap/box/violin/line/bar/...
        # 每个图元只从 f["columns"] 取列名，不写死结论；轴题/单位/误差线/显著性从 f 读取
        # 完成后：fig.savefig(f"work/figures/{f['figure_id']}.svg", bbox_inches='tight')
        #        fig.savefig(... .pdf, bbox_inches='tight')
        #        fig.savefig(... .png, dpi=300, bbox_inches='tight')
        plt.close(fig)
```

模板不预设数据集、列名与结论，只固化字号/配色/导出口径；具体映射从
`figure_model.json` 与 `data_profile.json` 读取，保证"图元 ↔ 源数据"一一对应。

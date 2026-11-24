# figure_spec.json 语法与检查清单

本文件定义 `scripts/main.py render` 的输入规范，是「数据 → 图稿」可重绘的关键：改 spec 重跑 render 即重绘；图例（legend）与实体（entities）共享颜色与映射，避免图例与数据不一致。

## 1. 顶层结构

```json
{
  "figure_id": "figure01",
  "title": "图标题（可空）",
  "canvas": {"width_px": 2400, "height_px": 1400, "dpi": 300},
  "font": {"family": "Arial, Helvetica, sans-serif", "base_px": 28},
  "background": "#ffffff",
  "entities": [],
  "relations": [],
  "groups": [],
  "legend": {"title": "Legend", "x": 0.74, "y": 0.60, "w": 0.22, "items": []},
  "annotations": []
}
```

坐标为画布归一化坐标（0–1，x 向右、y 向下）；未给 x/y 的实体由确定性网格布局自动放置。

## 2. 实体 entities

| 字段 | 说明 |
|-|-|
| id | 唯一标识，relations 引用它；provenance 的 element 也用此 ID |
| label | 可编辑文本，支持 `\n` 换行 |
| kind | `node` / `process` / `annotation`（当前仅标注用，不改变绘制） |
| shape | `rounded`（默认）/ `rect` / `ellipse` / `circle` / `diamond` / `hexagon` |
| x, y, w, h | 归一化中心坐标与尺寸（缺省自动布局） |
| fill, stroke, text | 颜色（缺省按关系/分类主题） |
| group | 归属的分组 ID；同组实体自动画进一个包含边界框（表达「包含」） |
| source_ref, quote | 来源定位与逐字原文（写入 provenance） |

## 3. 关系 relations

| 字段 | 说明 |
|-|-|
| id, from, to | 关系 ID 与两端实体 ID |
| kind | `activation` / `inhibition` / `flow` / `causal` / `sequence` / `mapping` / `association` |
| directed, style | 默认由 kind 决定箭头；style 可强制 `arrow` / `tee` |
| label, color, stroke_width | 可选 |

方向语义（评分重点，逐条核对）：`activation`=激活（实线箭头）；`inhibition`=抑制（T 形端，**不得画成箭头**）；`flow`/`sequence`=流程/时序（单向箭头）；`causal`=因果（箭头）；`mapping`/`association`=关联（虚线，无方向或箭头）；`contains`=包含（用实体 `group` + `groups` 表达，**不得画成并列**）。

## 4. 包含（groups）

```json
{"id": "phase1", "label": "Phase 1", "members": ["n1", "n2"]}
```

members 可省略，此时自动收集 `group == "phase1"` 的实体。渲染为虚线圆角边框 + 左上角标题，成员在框内。

## 5. 图例 legend

items 每项 `{"label", "color", "marker", "source_ref", "quote"}`；marker 取 `arrow` / `tee` / `line`（色块）/ `circle`。图例项必须与 entities/relations 实际使用的颜色、形状同源（同一份 spec，不复制第二份值）。

## 6. 导出预设（期刊参数自检项）

| 参数 | 默认 | 写入 export_files |
|-|-|-|
| 尺寸 | 2400×1400 px（按需调整） | width_px / height_px |
| 分辨率 | 300 dpi | dpi |
| 色彩空间 | RGB | color_space |
| 字体 | 嵌入/替换/未嵌入 | font_check：PNG 栅格化为 embedded；SVG 引用字体为 none/replaced |
| 线宽 | ≥0.5 pt 等效（base_px 驱动） | note 记录 |

## 7. 交付前自检（对应评分维度）

- [ ] entities 每个 ID 都能在 provenance 定位（locator + 逐字 quote）
- [ ] relations 方向与 kind 语义一致，抑制用 tee 不用箭头
- [ ] groups 的成员与「包含」语义一致，未误画为并列
- [ ] legend 与 entities/relations 的颜色、marker 逐项同源
- [ ] 图中无来源不存在的基因名/浓度/时间点/单位
- [ ] SVG 可被 XML 解析、含 `<g>` 图层与可编辑 `<text>`
- [ ] export_files 逐项填写尺寸/dpi/色彩空间/font_check，并真实产出可打开文件
- [ ] 缺渲染器时 export_files 如实记 failed，status 不冒充 complete

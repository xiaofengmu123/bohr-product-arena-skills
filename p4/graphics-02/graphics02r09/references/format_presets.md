# 导出预设与数据映射约定

## 数据映射与证据 locator
- locator 规范：`材料相对路径:行号` 或 `表名#列#行`（1-based）；每个关键图元在 `data_map` 记录 `element → locator + quote`。
- 冲突来源都记录并标 `source_conflict`；缺失标 `not_reported` 或放入 `pending_human`。
- 不虚构引用、DOI、数值；缺就写 `pending_human` 或 `not_reported`。

## 期刊/演示导出预设
| 场景 | 源文件 | 导出 | 关键参数 |
|---|---|---|---|
| 论文 | 分层 SVG/EPS | PDF + PNG | PNG≥300 dpi、字体嵌入、白色/透明背景明确、单栏或双栏宽 |
| 汇报 | SVG/PPTX | PDF + PNG(16:9) | ≥150 dpi、对比度足够、正文字号≥12 |
| 投稿 | SVG | PDF（矢量） | 文字未转曲、色彩空间（CMYK/RGB）注明 |

## matplotlib 确定性（`scientific-python` 可用时）
- `matplotlib.use("Agg")`；`np.random.seed(0)`；`bbox_inches="tight"`；`dpi=300`。
- `font.family` / `font.sans-serif` 用 font-discovery 返回的可用字体，找不到中文字体则回退英文标签。
- 保存后自检：SVG 含 `<text>`；PDF 以 `%PDF-` 开头且文字可选；PNG 魔数为 `\x89PNG`。

## 字体
- 中文优先 font-discovery 返回的可用字体（如 Noto / 文泉驿 / SimHei），写入导出参数；找不到→英文标签或写明乱码风险。

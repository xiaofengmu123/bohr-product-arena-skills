# 导出预设、证据溯源与鲁棒性矩阵

## 1. 导出预设（按目标场景选一档，写入 `sources`/导出参数）

| 场景 | 格式 | 分辨率/参数 | 说明 |
|---|---|---|---|
| 期刊投稿 | SVG（源）+ PDF + PNG | PNG 300–600 dpi，CMYK 或 RGB 按期刊，嵌入字体 | PDF 矢量文字可选中 |
| 汇报/海报 | PPTX（源）+ PNG | 宽 ≥2400 px，RGB，白底 | 可编辑元素保真 |
| 快速预览 | SVG + PNG | 150 dpi，RGB | 仅内部核对，不冒充正式件 |

导出参数（matplotlib 示例，标注为示例）：`dpi=300, facecolor='white', transparent=False`；PDF 用 `pdf.fonttype=42` 嵌入字体。中文优先用 `font-discovery` 找到的可用字体，找不到则回退英文标签或注明乱码风险。

## 2. 证据溯源格式

`evidence_map` 元素规范：

```json
{
  "key": "fig2a.bar3.height",
  "locator": "data/fig2.csv#row12,col4",
  "quote": "14.2",
  "target_element": "图 2a 第 3 根柱的高度",
  "confidence": "verified | inferred | pending"
}
```

- `locator` 用“文件路径 + 定位”表达：`materials/paper.pdf#p3`、`data.csv#row12,col4`、`table.md#row5`。
- `quote` 必须是材料中的逐字片段，不是转述；无片段则写 `not_reported`。
- `confidence` 三级：`verified`（与源逐字一致）、`inferred`（由源推导）、`pending`（待人工确认）。
- 冲突来源并存，两个都记录并标 `source_conflict`，不静默选一个。

## 3. 鲁棒性矩阵（main.py 已实现，selftest.py 验证）

| 场景 | 行为 | 退出码 | 结果 |
|---|---|---|---|
| 空输入目录 | 骨架落盘，`insufficient_evidence` | 0 | 合法 result.json |
| 坏 JSON | `errors` 记 `bad_json`，继续 | 0 | 合法 result.json |
| 坏编码 | utf-8-sig/utf-8/gb18030 逐级回退，latin-1 兜底 | 0 | 合法文本 |
| 缺参数 | 缺省 /input、/app/output、/app/work；env 可覆盖 | 0 | 正常执行 |
| 超大文件 | 超 2MB 跳过，`errors` 记 `oversized` | 0 | 不读入内存 |
| 写盘失败 | `errors` 记 `write_failed`，不崩溃 | 0 | stdout 仍给摘要 |
| 重复运行 | `sort_keys` + 固定排序，同输入同输出 | 0 | 确定性 |

## 4. 最小调用示例（示例，非正式案例）

```bash
python scripts/main.py bootstrap --input /input --out /app/output --work /app/work
# -> /app/output/result.json（partial 骨架）、/app/work/contract.json、bootstrap_report.json
python scripts/extract_tables.py --input /input/data.csv --name fig2a --out /app/work/t_fig2a.json
# -> 列类型、缺失/量级 issue、映射提示
python scripts/main.py check --out /app/output --work /app/work
# -> /app/work/check_report.json（ok/failures/warnings）
python scripts/selftest.py
# -> 包级自检结果（空输入/坏 JSON/坏编码/缺参数/超大文件/交付校验）
```

以上输出均为示意，不代表任何正式案例结果。

## 5. 无 matplotlib 时的手写 SVG 兜底模板（示例）

当科学 Python 环境不可用时，用标准库手写分层 SVG；文字必须为 `<text>` 节点而非转曲。下为最小骨架，仅供结构参考，须按实际数据逐图元回填：

```xml
<svg xmlns="http://www.w3.org/2000/svg" width="800" height="500"
     viewBox="0 0 800 500" font-family="sans-serif">
  <rect x="0" y="0" width="800" height="500" fill="#ffffff"/>
  <!-- 图层顺序：背景 -> 数据图元 -> 轴线 -> 标注，便于返修与分层导出 -->
  <g id="data-layer">
    <rect id="bar1" x="120" y="180" width="60" height="220" fill="#4c72b0"
          data-source="data.csv#row2,col2" data-value="14.2"/>
  </g>
  <g id="axis-layer">
    <line x1="100" y1="400" x2="720" y2="400" stroke="#000000" stroke-width="1.5"/>
  </g>
  <g id="annotation-layer">
    <text id="ylabel" x="40" y="200" font-size="14">mean (ng/mL)</text>
  </g>
</svg>
```

手写 SVG 必须同步给出 `data-source` 定位与 `evidence_map`；PNG/PDF 若无法真实渲染，如实写 `uncertainty` 并保持 `status=partial`，不伪造导出件。

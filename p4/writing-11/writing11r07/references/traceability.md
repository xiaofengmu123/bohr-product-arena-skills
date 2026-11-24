# 证据溯源与版本边界规范（traceability）

本文定义 `result.json` 中来源映射、对齐记录与版本边界的写法，使任意第三方可逐条复核译文而不需信任作者自述。

## 1. 定位符（locator）约定

每个来源定位必须是"文件路径 + 页/行"，可稳定复现：

- 文本类：`material.md#L12-L15`（行号）、`paper.txt#para3`（段落）。
- PDF：`paper.pdf#p3`（页码，PDF 工具提取后另附 `work/source_text/paper.txt#L12`）。
- 分段后：直接用 `source_map.json` 的 `segments[].id`，形如 `paper.txt#L12-L15`。

禁止只写文件名或只写"原文"。无页码能力时写"未确认页码"，不虚构页码。

## 2. 逐字 quote 规则

- `quote` 必须是从原文**原样复制**的片段，不增删字符、不改写、不翻译。
- 若原文跨行或含换行，用 `␊` 或 `\n` 显式标记；不得静默合并。
- 若同一结论对应多处原文，分别写多条记录，不合并成一条模糊引用。
- 找不到原文的结论：`quote` 写 `null`，`note` 写"原文未找到，待核"，并进 `uncertainty`。

## 3. fact_source_map 字段与 kind

每条记录：

```json
{
  "item": "样本量 n=128",
  "kind": "number",
  "value": "128",
  "source": "material.md#L12",
  "quote": "n = 128 participants",
  "note": "译文中保留 128，未四舍五入"
}
```

`kind` 取值与 `value` 含义：

| kind | value 示例 | 说明 |
|-|-|-|
| `number` | 数字原文与归一值 | 数字、单位、量级、范围、年份、统计口径 |
| `term` | canonical 译法 | 术语/缩写锁定，引用 glossary.json |
| `symbol` | 公式符号 | 变量/算子/单位符号 |
| `citation` | 引用标记 | 作者-年份/编号/et al./上标 |
| `figure` / `table` | 图表编号 | 图题/表题及其编号对应 |
| `formula` | 公式原文 | 公式及其编号原位保留 |
| `claim` | 关键论断 | 否定范围/因果强度/模糊限制语/归属 |

推断与原文必须区分：不是逐字从原文来的内容，`note` 写"推断，待核"。

## 4. 双语对齐记录（alignment.json）

句段级对照，每项：

```json
{
  "segment_id": "material.md#L12-L15",
  "source": "The root mean square error (RMSE) was 0.32.",
  "target": "均方根误差（RMSE）为 0.32。",
  "type": "paragraph",
  "note": null
}
```

要求：段落顺序与原文一致；`type` 与 `source_map.json` 一致；公式、表格、题注、脚注、参考文献条目各成一段，不并入正文。

## 5. version_boundary 字段

- `modifications`：每项 `{what, why, before_after}`，记录"改了什么、为什么、前后差异"。局部修订只写改动点，用 locator 标位置，不整段覆盖。
- `human_confirm`：需要人工确认的项（术语冲突、数字疑似错、引用找不到、语义边界模糊）。
- `material_gaps`：材料缺失/无法判断（缺图、缺公式、缺参考文献、缺目标语言约束）。
- `anomalies`：异常（编号不连续、结论冲突、编码错误、渲染失败）。
- `editable`：布尔。true 表示工件是可编辑文本/Office；false 表示只读截图或仅预览。

## 6. 冲突与不确定信息的登记

- 同一指标两个来源不一致：两条都记录，并标 `source_conflict`，不擅自取其一。
- 材料不足、无法判断、引用找不到原文、数字与原文不符：一律登记 `uncertainty`，`status` 不得为 `success`。
- 缺软件（渲染/编译工具）：在 `sources`/`uncertainty` 写"未渲染预览，原因：缺少 X"，禁止谎称成功。

## 7. 可复现性

- 脚本只用标准库；`checks`/`selftest` 无随机性、无网络、无时间敏感逻辑（除 `change_log`/`runtime` 时间戳）。
- 同一输入重复运行，除时间戳字段外，`result.json` 的结构性字段一致。
- 复用者只需：把输入放到目录 → `python3 scripts/main.py init` → 按 SKILL.md 流程翻译 → `checks` → `finalize`。

# 自查清单与已知失败模式（academic-bilingual-translator）

发布/交付前必须逐条勾选。对应评分维度见 SKILL.md 第 5 节。

## 逐项自查

### 产物完整性
- [ ] `/app/output/result.json` 存在、合法 JSON、UTF-8、可被 `json.load` 读取。
- [ ] 含 `case_id`（缺失则为 `null`），与 `/input/case.json` 一致。
- [ ] 含 `deliverables` / `fact_source_map` / `version_boundary`（或 `case.json` 声明的 `output_contract` 全部字段）。
- [ ] 含 `status` / `sources` / `uncertainty` / `pending_human` / `change_log`。
- [ ] `deliverables[].path` 指向的文件真实存在；Office/图类工件有渲染 PDF/PNG 预览并写入 `deliverables`。

### 事实与来源映射（每项必须有 locator + 逐字 quote）
- [ ] 每条数字、术语、引用、公式、图表编号、关键论断都有 `source`（文件路径+页/行）与 `quote`（原文逐字片段）。
- [ ] 未新增材料外事实；推断与原文明确区分（`note` 标注"推断/待核"）。
- [ ] 存疑引用、找不到原文、数字与原文不符均已登记 `uncertainty` / `pending_human`。

### 术语与数字
- [ ] `glossary.json` 每条含 `canonical` + `variants` + `translation` + `source` + `quote`。
- [ ] 同一术语在摘要、正文、图表题注、附录保持同一译法；同一英文词跨学科异译已显式说明。
- [ ] 数字、单位、量级、范围、年份、统计口径原样落地；万/亿与 million/billion 换算正确。

### 对齐与结构
- [ ] `alignment.json` 句段级对照，段落顺序、图表编号、题注、脚注对应关系完整。
- [ ] 公式、表格、题注、脚注、参考文献条目保留在原位，未被删除或重排。

### 版本与边界
- [ ] `modifications` 每项含 `what`（改了什么）/ `why`（为什么）/ `before_after`（差异）。
- [ ] 未确认项进 `human_confirm`；材料缺口进 `material_gaps`；异常进 `anomalies`。
- [ ] `editable` 为 true 时，工件是可编辑文本/Office，而非只读截图。

### 一致性与健壮性
- [ ] 已运行 `scripts/main.py checks` 并保存 `consistency_report.json`。
- [ ] 数字缺失/新增、引用缺失、句段类型计数差异已逐条处理或交人工确认。
- [ ] 已运行 `scripts/selftest.py`：空输入、坏 JSON、缺参数、超大文件全部退出码 0 且产出合法结果。
- [ ] 重复运行同一输入，`checks`/`selftest` 结果一致（无随机性）。

## 已知失败模式（对号入座处置）

| 现象 | 原因 | 处置 |
|-|-|-|
| 译文章节/图表/公式丢失 | 未按 `source_map.json` 逐段处理 | 先跑 `extract`，按句段定位顺序翻译 |
| 同一概念多种译法、缩写跳变 | 未维护 glossary | 建 `glossary.json`，正文只写 canonical，variants 显式声明 |
| 数字四舍五入或单位错配 | 未跑数字核对 | 跑 `checks`，按 `number_inventory.json` 复核 |
| 万/亿与 million/billion 换算错 | 未做量级归一 | 用 `canonical_number` 归一后再比 |
| 引用/参考文献漏译或错位 | 未核对引用清单 | 按 `citation_inventory.json` 逐条保留 |
| 改一处导致段落脱节 | 未用句段定位 | 用报告中的"文件:行号"定位受影响段落 |
| 引用找不到原文却照抄 | 未核对文献 | 登记存疑引用，不改写结论 |
| PDF 只凭文件名臆测 | 未真实解析 | 用平台 pdf-text-tool 提取正文，不读标签 |
| 中途崩溃全丢 | 未先落盘骨架 | 前 3 分钟 `scripts/main.py init` 落盘 partial |
| 空输入/坏 JSON 崩溃 | 未做健壮性处理 | 交给 `init` 的容错路径，`status` 如实标注 |

## 术语表模板（glossary.json）

```json
{
  "terms": [
    {
      "canonical": "均方根误差",
      "variants": ["均方根误差", "RMSE", "root mean square error"],
      "translation": "均方根误差",
      "source": "material.md#L12",
      "quote": "root mean square error (RMSE)"
    }
  ]
}
```

规则：`canonical` 为全文统一写法；`variants` 列出允许出现的同义/缩写写法；若正文同时出现多个 variant，`checks` 只做提醒，需人工统一或显式说明；中文术语的抽取由 Agent 在阅读原文时完成并写回 glossary，脚本仅负责英文缩写/展开式脚手架。

## 数字核对说明（脚本能力边界）

`extract_numbers` 是**启发式**工具：它归一化数字、量级（万/亿 vs million/billion）与常见单位，输出 canonical 值供跨语言比对。以下情况会漏报或误报，须人工复核：

- 上标指数（如 `3.2×10⁶`）与 Unicode 分数（如 ½）不识别。
- 有效数字位数的精度取舍不做判定（`1.20` 与 `1.2` 归一后相同）。
- 数字被作者有意改写、合并或拆分时，会标记为"缺失/新增"而非错误。
- 大于 15 位有效数字的大数采用浮点表示，比较仅为近似。

所有 `checks` 输出都是 warning 级别的**复核提示**，不是自动评分结论；是否真的错误须由人/Agent 对照原文决定。

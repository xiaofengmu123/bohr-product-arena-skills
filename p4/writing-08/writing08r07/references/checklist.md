# 六要素覆盖表、参数表与证据映射模板

本文件是 SKILL.md 第 5、6 节的执行模板。写作时逐项对照，缺口写 `MATERIAL GAP` 或 `not_reported`，冲突标 `source_conflict`。

## 1. 六要素覆盖表（结果完整性）

| 要素 | 必写内容 | 缺失时的标注 |
|---|---|---|
| 方法步骤 | 每步 输入→操作→输出→判断条件；不把多步压成一句 | `MATERIAL GAP: 第 N 步输出/判断条件未给出` |
| 实验设置 | 训练/验证/测试分开；硬件、软件版本、运行环境、随机种子、重复次数 | `not_reported`；条件串台标 `source_conflict` |
| 参数与来源 | 名称、取值、单位、作用对象、来源（配置/日志/作者确认） | 无来源参数标 `pending_human` |
| 数据集与划分 | 名称、版本、规模、预处理、划分比例、随机方式、各子集用途 | 版本不明写 `not_reported` |
| 对照实验 | 基线名称与版本、是否同预算、评价指标、统计方式 | 无可比性则注明"不可比：预算不一致" |
| 可复现与限制 | 代码版本、环境、命令、数据获取方式；限制落到具体失效场景 | 限制不得用"可能存在问题"类套话 |

## 2. 参数表模板（正文与表格逐条对齐）

```markdown
| 参数 | 取值 | 单位 | 作用对象 | 来源 |
|---|---|---|---|---|
| learning_rate | 1e-4 | — | 优化器 | config.yaml#L3 |
| batch_size | 32 | — | 数据加载 | config.yaml#L4 |
| seed | 42 | — | 数据划分/初始化 | config.yaml#L5 |
```

规则：正文中的每个数值必须能在表或 `preserved_facts.json` 找到同一 locator；表格中的值必须与材料逐字一致（不改单位、有效数字、统计量）。

## 3. 证据映射模板（evidence_map.json）

正文事实句挂 `[S#]`，`evidence_map.json` 每条含 locator + 逐字 quote：

```json
{
  "S1": {
    "element": "parameter",
    "name": "learning_rate",
    "locator": "materials/config.yaml#L3",
    "quote": "learning_rate: 1e-4"
  },
  "S2": {
    "element": "dataset",
    "name": "ImageNet-1k",
    "locator": "materials/config.yaml#L6",
    "quote": "dataset: ImageNet-1k"
  }
}
```

校验要点（与 `scripts/main.py validate` 一致）：
- locator 非空且可指向实际材料位置；文本材料形如 `materials/<file>#L<line>`，PDF 形如 `<file>#p<page>`。
- quote 必须是材料逐字片段，不得改写、不得补充。
- 同一指标两个来源不一致 → 两条都记录并标 `source_conflict`。

## 4. preserved_facts.json 模板

```json
{
  "facts": [
    {
      "type": "number_unit",
      "value": "1e-4",
      "unit": null,
      "quote": "1e-4",
      "locator": "materials/config.yaml#L3"
    }
  ]
}
```

`preserved_facts.json` 与 `extracted_facts.json` 的关系：前者是**已逐条核实**的事实，后者是**候选未核实**事实。写作时只把核实过的候选移入 preserved_facts，禁止直接用未核实的候选填正文。

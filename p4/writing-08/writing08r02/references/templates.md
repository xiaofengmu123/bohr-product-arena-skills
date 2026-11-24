# 六要素覆盖表与模板

本文件是 `SKILL.md` 第 5 节的可复用模板：写作时按表逐项覆盖，避免空节与占位空话。

## 1. 六要素覆盖表（结果完整性）

| 要素 | 必须写出的字段 | 常见漏写（扣分点） |
|---|---|---|
| 方法步骤 | 每步：输入→操作→输出→判断条件 | 多步压成一句；无判断条件；无输出 |
| 实验设置 | train/val/test 分开；硬件、软件版本、运行环境、随机种子、重复次数 | 条件串台；缺种子/版本/重复次数 |
| 参数与来源 | 名称、取值、单位、作用对象、来源 | 正文与表格不一致；参数无来源 |
| 数据集与划分 | 名称、版本、规模、预处理、划分比例、随机方式、各子集用途 | 版本含混；缺划分比例/随机方式 |
| 对照实验 | 基线名称与版本、是否同预算、评价指标、统计方式 | 只说"优于基线"，无同预算/统计方式 |
| 可复现与限制 | 代码版本、环境、命令、数据获取方式；限制指向具体失效场景 | 复现清单缺命令/数据获取；限制是套话 |

## 2. 参数表模板

```markdown
| 参数 | 取值 | 单位 | 作用对象 | 来源 |
|---|---|---|---|---|
| learning_rate | 1e-4 | — | 优化器 | config.yaml#L3 |
| batch_size | 32 | 样本 | 训练 | config.yaml#L4 |
| seed | 42 | — | 随机初始化/划分 | config.yaml#L5 |
```

来源列写 locator（相对 `/input/`）；材料无出处时写 `pending_human`，数值不明写 `not_reported`，两个来源不一致并列并标 `source_conflict`。

## 3. 证据映射模板（evidence_map.json）

```json
{
  "S1": {
    "claim": "学习率为 1e-4",
    "locator": "exp/config.yaml#L3",
    "quote": "learning_rate: 1e-4",
    "source_conflict": []
  }
}
```

规则：claim 是正文事实句；locator + quote 逐字来自材料；同一 claim 多个来源时都列进 `sources` 数组，冲突标 `source_conflict`。

## 4. 数值/统计量保全清单（preserved_facts.json）

```json
[
  {
    "kind": "statistic",
    "value": "0.742 ± 0.011",
    "unit": "ACC",
    "n": 5,
    "locator": "results.txt#L12",
    "quote": "ACC = 0.742 ± 0.011 (n=5)"
  }
]
```

数值逐字复用，不改单位、有效数字、统计量（mean±sd、n、p 值）；限定词强度（"显著/可能"）不升不降。

## 5. 常见失败模式（可靠体验）

- **补数**：正文出现材料没有的数值 → 改 `not_reported` 或 `MATERIAL GAP`。
- **条件串台**：train/val/test 的种子、划分、指标混用 → 分别成段、分别来源。
- **空泛限制**："存在一定局限" → 改成"当数据分布偏移时，固定 seed 的划分结果不可外推"。
- **静默跳过**：坏 JSON/缺 PDF 文本不记录 → 必须写入 `errors`/`degradations`。
- **伪造状态**：材料不足却写 `ok` → 如实写 `partial` 或 `insufficient_evidence`。

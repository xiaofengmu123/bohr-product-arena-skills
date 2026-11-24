# 中间表示与可复核设计说明

本 Skill 的核心设计取舍是：**不追求“一步到位地生成结果”，而是把每一步的判定依据
沉淀为结构化中间产物，让最终 `result.json` 可以由中间产物重新推导出来。** 这与
“生成一条好答案”的端点方案不同——它把“引用是否真实、状态是否诚实、能否复跑”从
提示词诉求变成可机器核对的数据结构。

## 1. 中间表示链条

每次运行至少产出以下六个中间表示（都在 `/app/work`，相对路径，仅标准库）：

| 文件 | 内容 | 作用 |
|---|---|---|
| `plan.json` | 预算（900s / 64 请求）、四阶段 deadline、每步产物 | 把“前 3 分钟先落盘骨架”固化为可执行计划 |
| `case_summary.json` | case_id、output_contract、questions、materials、错误 | 输入契约的规范化快照，供后续步骤引用 |
| `inventory.json` | 逐文件 sha256/类型/权限/元数据 + `library` 六维度 | 唯一事实来源，finalize 整段回填 |
| `evidence_index.json` | 文本行数/preview、PDF 页号-字符偏移映射 | 支撑 `#L12`、`#p3` 的可复核定位 |
| `answers.json` | 逐问答案 + evidence（file/sha256/locator/quote）+ uncertainty | Agent 真正“写证据”的落点，与脚本解耦 |
| `trace.json` | 每一步 step/action/status/time | 操作记录与 `result.json → change_log` 同源 |

`finalize.py` 是纯合并器：它不重新盘点，只把 `answers.json` 与 `inventory.json`
按确定性规则并入 `result.json`。因此任何结论的回链都能沿
`answers → inventory → 原文件` 或 `answers → evidence_index → 原文件` 复核。

## 2. 为什么这样设计

- **失败可解释**：`scaffold` 先写一份合法 partial 骨架；后续任何一步中断都不会留下
  空产物，`status` 反映到哪一步为止。
- **引用可核验**：`quote` 逐字 + `locator` + `sha256` 三件套，改文件名不影响回链；
  PDF 的页码来自 pdftotext 换页符拆分，不是人工估计。
- **重复可复现**：所有列表固定排序、`sort_keys=True`、LF 换行、固定 sha256；
  时间戳只进 `runtime`/`change_log`，不影响内容确定性（`selftest` 已覆盖）。
- **契约可回填**：`output_contract` 原样保留，同时每个键在 result 顶层拍平为
  `None` 占位，finalize 用 `answers.output` 的值填充；未知键永不删除。

## 3. answers.json 的最小契约

```json
{
  "output": {"<output_contract 键>": "<该键的值>"},
  "answers": [
    {
      "question": "…", "answer": "…",
      "evidence": [
        {"file": "materials/paper.md", "sha256": "…",
         "locator": "materials/paper.md#L12-L15",
         "quote": "逐字片段", "confidence": "certain"}
      ],
      "uncertainty": "certain", "missing": null
    }
  ],
  "uncertainty": [
    {"level": "conflict", "item": "…", "reason": "两来源不一致", "sources": ["a", "b"]}
  ]
}
```

`finalize` 只信任这里的 `file/sha256/locator/quote`，不会替 Agent 补造；缺了就
`insufficient_evidence`，读不到就 `unreadable/needs_ocr`，两者都进入
`pending_human`。

## 4. 与参考实现的最大差异

参考方案把证据回链当作 SKILL.md 里的“约定”；本方案把它**物化为可哈希、可排序、
可由 `selftest.py` 断言的中间文件**，并让 `finalize` 只做合并、不做重判。结果是：
换一个人拿同一份 `/app/work` 与 `answers.json`，即可逐步重放并回到同一个
`result.json`——这比“写一份漂亮的说明”更接近题目要求的“换一个人能重新走一遍”。
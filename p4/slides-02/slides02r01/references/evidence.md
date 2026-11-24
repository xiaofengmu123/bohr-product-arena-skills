# 证据溯源规范（evidence-trace）

本规范是本 Skill 的核心设计取舍：**每条关键结论都带 locator + 逐字 quote**，不靠"我核对过原文"这类自述，而靠可机械复核的定位符与逐字片段。下方为接口与 schema 说明，示例仅为示意，不是任何正式案例的答案。

## 1. 定位符（locator）语法
- 论文正文段落：`paper.pdf#p7` 或 `materials/paper.md#p3`（文件 + 页码/段落）。
- 图：`Fig. 3`；表：`Tab. 2`；章节：`Sec. 3.2`；数据表文件：`data/table2.csv#row12`。
- 组合定位：`Fig. 3@paper.pdf#p7`，表示图 3 所在页与来源。
- 规则：locator 必须能指回一个真实存在的材料位置；指向不存在的文件/页码即视为未定位，记入 `uncertainty`。

## 2. 逐字引用（quote）规则
- quote 是从提取正文、OCR 文本、图注或数据表中原样截取的连续片段，逐字相等，含标点与大小写。
- 不允许：按印象复述、改写、翻译、省略后用省略号拼接、把"约 42%"写成"42%"。
- 数值型 quote（`quote_type=number`）必须是原文数字串，如 `"42.3 ± 1.2"`；单位与误差区间一并保留。
- 无法逐字引用的结论（如归纳的叙事角色）用 `quote_type=paraphrase`，quote 仍给最接近的原文片段，并在 claim 中标明这是归纳。

## 3. evidence_map 细粒度 schema
```json
{
  "page": "4",
  "role": "result",
  "claim": "第 4 页主张：方法 X 在基准 Y 上达到 42.3%",
  "refs": ["Fig. 3", "Tab. 2"],
  "locator": "Fig. 3@paper.pdf#p7",
  "quote": "X achieves 42.3 ± 1.2 on benchmark Y",
  "quote_type": "verbatim",
  "quote_verified": true,
  "quote_hit": "paper.txt",
  "confidence": "high",
  "checked": true,
  "t": "2026-11-23T10:00:00+08:00"
}
```
- `page` 与 `role` 唯一标识一条证据；同页同 role 重复写入时更新而非追加。
- `quote_verified=true` 表示 quote 已在提取正文中用 `str.find` 逐字命中；`quote_hit` 记录命中文件名。
- `checked=true` 表示页面数字/图注/引用编号已与原文逐字复核；未复核必须保持 false。

## 4. quote 核验流程
1. 步骤 1 把提取正文存为 `$WORK_DIR/corpus/paper.txt`（或相应 .md/.txt）。
2. `record.py evidence --quote "..."` 自动在 `$WORK_DIR/corpus/` 与 `$WORK_DIR/` 下比对。
3. 命中则写 `quote_verified=true`；未命中仍写 `false` 并把条目追加到 `uncertainty.unresolved`，不静默通过、不删除证据。
4. 若 quote 来自 OCR/渲染图，正文语料比对不到属正常，用 `--no-verify` 显式跳过并说明原因。

## 5. 冲突与缺失处理
- 同一指标两个来源不一致：两条证据都记录，各带自己的 locator+quote，并标 `source_conflict`；不得只留一条。
- 材料未报告某数值：写 `not_reported`；需要人工判断：写进 `pending_human`，不进入页面主张。
- 图缺失/纯位图无数据：只引用原图并给图号+页码定位，不重绘假曲线、不补数值。

## 6. 禁止事项
- 不虚构引用、DOI、URL、数值；缺失就写 `pending_human` 或 `not_reported`。
- 不把"示意示例"当作正式案例结果；示例必须显式标注。
- 不把 paraphrase 冒充 verbatim；quote_type 必须如实填写。

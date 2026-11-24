# 论断—来源记录结构与确定性约定

## 1. fact_source_map 条目结构

每个关键结论/数字/引用/图表/修改动作对应一条记录：

```json
{
  "claim_id": "fsm-<sha12 前12位>",
  "claim_text": "对材料中事实/规格的转述（不含超出材料的推断）",
  "quote": "材料中逐字摘录的原文片段",
  "locator": "材料文件路径 + 章节/段落/页码，如 paper.md#结果",
  "source_ref": "sources 数组中的 id",
  "evidence_level": "template_spec | reported_value | single_study | abstract_only | inferred",
  "modification": "本次改稿对原文的动作：kept | renumbered | restyled | corrected | added_citation | none"
}
```

- `claim_id` 用内容哈希（`stable_id("fsm", claim_text)`），不使用序号：重排、重跑都不改变 ID。
- `quote` 必须逐字、可由 `python -B scripts/main.py verify` 在材料文本中定位（空白折叠后子串匹配）。
- `evidence_level=inferred` 的条目必须同时在 `uncertainty.claims_requiring_confirmation` 中登记。

## 2. 常见失败类型与处置

| 失败类型 | 检测方式 | 处置 |
|-|-|-|
| 摘录找不到 | `main.py verify` 警告 | 修正 locator，或标 `limitations` |
| 缺字段 | `main.py verify` 硬错误 | 补齐字段后重跑 |
| 冲突证据 | 人工/模型比对 | 并列写入 `conflicting_evidence`，正文用“存在争议/尚不明确” |
| 二手转引 | 元数据核验 | 记 `verification="unverified"` + `pending_human` |
| 材料缺正文 | 只有摘要 | 只写“摘要表明”，`material_gaps` 登记 |
| 缺软件/字体 | 工具不可用 | `degradations` 登记，不伪造渲染文件 |

## 3. 确定性约定（本 Skill 的核心设计取舍）

- 序列化一律走 `core_impl.canonical_json`：`sort_keys=True`、`ensure_ascii=False`、2 空格缩进、UTF-8、换行结尾。
- 标识符一律内容哈希（SHA-256 前 12 hex），无计数器、无时间戳。
- `change_log` 用固定 `step` 编号 + `action` 幂等去重：0=scaffold、10=格式规格提取、20=内容单元清点、30=样式映射与成稿、40=导出、50=seal。
- 唯一允许跨运行变化的字段是 `runtime`（墙钟起止/耗时）与 `content_digest`；确定性比较一律剔除这两个键。
- 验证确定性：`python -B scripts/selftest.py` 会在合成输入上跑两遍整条管线，比较剔除 `runtime` 后是否逐字节一致；不一致即退出码 1。

## 4. 运行顺序（Agent 调用的最小闭环）

```bash
python -B scripts/main.py scaffold --case /input/case.json --out /app/output --work /app/work
# …… 分析、写产物、逐条回填 fact_source_map ……
python -B scripts/main.py verify   --result /app/output/result.json --case /input/case.json --report /app/output/verification_report.json
python -B scripts/main.py seal     --result /app/output/result.json
python -B scripts/main.py digest   --result /app/output/result.json
```

# 输出 schema 与可复核性约定（output_schema）

本文件定义 `result.json` 的字段语义、证据定位语法、quote 核验方式与确定性规则，供人工复核、二次实现与评分方对照。

## 1. result.json 顶层字段

| 字段 | 类型 | 含义 |
|---|---|---|
| `case_id` | string | 案例标识（`case_id`/`id`/`challenge_id` 自动识别，缺省 `unknown`） |
| `status` | enum | `ok | partial | insufficient_evidence | input_error` |
| `schema_version` / `engine` | string | 版本与引擎标识 |
| `overall_conclusion` | object | 总体结论与可执行动作 |
| `experiment_plan` | object | 结构化实验方案（从 case 提取或骨架） |
| `review` | object | 七类审查项数组，键名固定 |
| `check_ledger` | array | 每条审查项的一行式台账（用于 CSV 导出） |
| `plan_comparison` | array | 各版方案指标与字典序排名 |
| `version_diff` | array | 版本间差异摘要 |
| `contradictions` | array | 数值/样本量矛盾记录 |
| `evidence_and_risks` | object | evidence / risks / exception_handling |
| `confirmation` | object | safety / ethics / equipment_availability / expert_confirmation |
| `sources` | array | 输入文件清单（path/bytes/sha256） |
| `uncertainty` | array | 未确认/缺失项 |
| `pending_human` | array | 需人工处理项 |
| `errors` / `degradations` | array | 输入或解析异常 |
| `change_log` | array | 增量改进记录 |
| `runtime` | object | 只写预算，不写未测量的墙钟/请求 |
| `artifacts` | array | 产物文件清单 |
| `provenance` | object | 输入/结果 SHA-256 与确定性声明 |

**output_contract 回填**：若 `case.json` 含 `output_contract`（dict 或 list），其全部字段名原样回填；已由引擎生成的字段保留，缺失字段给类型缺省值（`null`/`[]`），不改名、不删未知键。

## 2. 证据定位语法（locator）

- JSON：`$.plans[0].steps[2]`、`$.contracts.equipment[0]`、`$.parameter_domains.rpm`
- 文本：`materials/<name>.md#<标题或行>`、`sources/<name>.pdf#p<页>`、`task.md#<行>`
- `#` 前为文件（`$`/`.` 开头映射到 `case.json`），`#` 后为路径或页码

## 3. quote 核验方式

每条 evidence 带 `verified` 布尔。核验规则（`SourceIndex.quote_verified`）：

1. 规范化空白后，`quote` 出现在来源原文中；
2. 或 JSON 源以 `\uXXXX` 转义存储中文时，`quote` 的转义形式出现在原文中。

quote 构造三条路径：字符串叶节点（逐字原文）、值字面量（数字回读原文中 `"key": value` 片段）、文本行（正则命中行）。`selftest.py` 会独立重算 `quote_verified` 并给出 `quote_ratio`。

## 4. 确定性约定

- 输出用 `json.dumps(..., sort_keys=True, indent=1, ensure_ascii=False)`，遍历字典按 `sorted()`、列表按序、集合转 sorted，无随机、无 `datetime.now`。
- `runtime.wall_clock_seconds` 恒为 `null`；墙钟/请求由平台记录，Skill 不写伪造耗时。
- `provenance.result_sha256` = 对 `result_sha256=None` 的 canonical JSON 求 SHA-256 后回填，因此同输入两次运行结果逐字节一致。
- `selftest.py --determinism` 对同一输入跑两次 pipeline，断言 `result_sha256` 相等。

## 5. Agent 人工修订格式（findings.json）

```json
{"review": {"equipment_materials": [{
  "claim": "……", "verdict": "conditional",
  "conditions": ["……"], "confidence": "medium", "needs_expert": false,
  "evidence": [{"locator": "……", "quote": "……", "kind": "plan", "verified": false}]
}]}}
```

合并规则：`verdict` 必须合法；firm 结论必须带 `evidence[].locator+quote`；非法项记入 `result.errors`，不合并；`id` 缺省按维度顺序编号；合并后 `change_log` 记录条数。

## 6. 降级编码（errors / degradations）

| 场景 | 记录 | status |
|---|---|---|
| 空目录 / 缺 `case.json` | `case_json_missing` | `insufficient_evidence` |
| `case.json` 解析失败 | `json_parse_error` / `json_repair_failed` | `input_error` |
| BOM/坏编码 | `fallback_decode(replace)` 进 `degradations` | 继续 |
| 超限文件 | `oversize` | 跳过并记录 |
| 缺软件/PDF 不可读 | `degradations` 标注"证据未能读取" | 继续 |
| 写盘失败 | 最小合法 JSON | 进程退出码 0 |

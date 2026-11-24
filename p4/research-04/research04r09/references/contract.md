# 输出契约与字段字典

本文是 `SKILL.md` 第 3 节的完整字段说明，供 Agent 生成与自检 `result.json` 时对照。

## 1. 顶层结构

```json
{
  "case_id": "string",
  "status": "ok|partial|insufficient_evidence|input_error",
  "<output_contract 声明的字段>": "原样回填，缺值给 null/[]",
  "research_question": {},
  "protocol": {},
  "time_trend": {},
  "topic_evolution": {},
  "representative_works": {},
  "research_gaps": {},
  "evidence_map": [],
  "conflicts": {},
  "sources": [],
  "uncertainty": [],
  "pending_human": [],
  "change_log": [],
  "limitations": [],
  "errors": [],
  "degradations": [],
  "runtime": {}
}
```

- `output_contract`：若 `case.json` 含该键（dict 或字段名 list），其全部字段**原样回填到顶层**；不改名、不删未知键。缺值给 `null`/`[]`。
- `case_id` 取值顺序：case.json 的 `case_id`/`id`/`challenge_id` → task.md 的 `challenge_id` → `"unknown"`。
- `status`：`ok` 仅在四类产物齐全且关键 claim 均有证据回链时给出；否则 `partial`；材料不足 `insufficient_evidence`；输入不可解析 `input_error`。

## 2. 四类可检查产物

### time_trend
```json
{
  "unit_note": "年份口径：在线发表/见刊/预印本，来源说明",
  "distribution": [{"year": 2020, "count": 3, "doc_ids": ["mat_001"]}],
  "windows": [{"period": "2018-2020", "count": 5, "note": "时间窗划分依据"}],
  "claims": [{"claim": "2021 年起产出量抬升", "support": "reported|not_reported|inferred", "evidence_ids": ["E001"]}]
}
```
- `distribution` 由脚本机械生成；Agent 需核验年份来源并区分**离散波动**与**真实拐点**。
- `support`：直接可引用 `reported`；需归纳 `inferred`；查不到 `not_reported`。

### topic_evolution
```json
{
  "stages": [{"stage": "萌芽期", "period": "2015-2018", "basis": "术语 X 首现且仅 2 篇", "terms": ["X"]}],
  "term_mapping": [{"canonical": "X", "variants": ["X'", "x"], "first_year": 2015, "last_year": 2024}],
  "events": [{"type": "emergence|merge|split|decline|terminology_drift", "term": "X", "period": "2019", "cause": "合并原因", "evidence_ids": ["E002"]}],
  "claims": []
}
```
- 每个 `event` 必须写**何时、因何**；同名异义（homonym）在 term_mapping 中分列不同 canonical，并在 uncertainty 说明归并口径。

### representative_works
```json
{
  "criteria": ["覆盖各阶段", "不以被引排序为准", "含长尾方法首创", "kind: origin/method/milestone/benchmark"],
  "stages": [{"stage": "成熟期", "period": "2020-2024", "works": [{"doc_id": "mat_003", "title": "...", "year": 2022, "rationale": "首个提出方法 Y", "kind": "origin"}]}]
}
```
- 每阶段按 `kind` 覆盖；`origin`=方法首创，`method`=方法类，`milestone`=里程碑，`benchmark`=基准/数据集。
- 入选 `rationale` 必须可说明，禁止只用总被引排序。

### research_gaps
```json
{
  "gaps": [{"id": "gap_01", "type": "open_question|method_failure|unresolved_conflict|coverage_absence", "description": "...", "exclusion_evidence": "检索式 + 排除条件 + 反例排查", "evidence_ids": ["E003"]}],
  "excluded_not_gaps": [{"candidate": "...", "reason": "已被某工作解决，见 E004"}]
}
```
- 每个空白必须写 `exclusion_evidence`；**证据排除的空白**写 `open_question`/`method_failure`/`unresolved_conflict`，**检索遗漏**写 `coverage_absence`。禁止把“没检索到”当空白。

## 3. evidence_map 与冲突

- 每条 `{id(E001起), claim, locator, quote, source_file, status, confidence}`。
- `locator` 形如 `materials/mat_001.txt#L12-L15`；`quote` 为原文**逐字**引用，非空即必须能在源文件定位。
- `status`：`reported`（原文直接陈述）、`inferred`（归纳）、`not_reported`（查不到）、`source_conflict`（两源不一致）。
- 冲突证据两方都记录并标 `source_conflict`，在 `conflicts` 给出分辨率或“未决”，不静默择一。

## 4. 元信息字段

- `sources`：材料清单（id/source_file/year/title/error）。
- `uncertainty[]`：归并口径、口径不确定、置信度低等。
- `pending_human[]`：需人工核验项。
- `change_log[]`：`{time, action, reason, before, after}`；记录骨架落盘与每次修改。
- `limitations[]` / `errors[]` / `degradations[]`：缺失材料、未核验类别、解析失败与降级。
- `runtime`：`{started_at, finished_at, elapsed_seconds, stages[]}`。

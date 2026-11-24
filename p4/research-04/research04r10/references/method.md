# 方法与中间表示（claims.json / ledger.jsonl / features.json）

本文件是深度分析阶段的“工作手册”：说明模型要写的 `claims.json` 结构、脚本维护的中间表示，以及年份归一、主题归并、空白判据与校验顺序。所有脚本仅用 Python 标准库。

## 1. 三种中间表示的分工

| 文件 | 谁写 | 内容 | 是否可再生 |
|-|-|-|-|
| `/app/work/ledger.jsonl` | 脚本（init/extract/compile 追加） | meta + doc + change 记录，只追加 | 由 init 重建 |
| `/app/work/features.json` | 脚本（extract 重写） | doc_years、terms、gap/conflict/work 候选，全部 `machine=true` | 完全可再生 |
| `/app/work/claims.json` | 模型（Agent） | 研究问题、协议、证据、四类产物、冲突、不确定性等 | 否，模型判断 |

`compile` 是纯投影：读三者 → `result.json` + `report.md` + `evidence_map.json` + `tables/*.csv`。result.json 从不手工拼装。

## 2. claims.json 完整结构（模型填写）

```json
{
  "status_hint": null,
  "research_question": {"question": "...", "scope": "...", "sub_questions": ["..."], "domain_note": "..."},
  "protocol": {"inclusion_criteria": ["..."], "exclusion_criteria": ["..."], "search_scope": "...", "date_definition_note": "..."},
  "evidence": [
    {"id": "E001", "claim": "判断或事实", "locator": "materials/mat_001.txt#L12-L15",
     "quote": "原文逐字片段", "source_file": "materials/mat_001.txt",
     "status": "reported", "confidence": "high"}
  ],
  "time_trend": {
    "unit_note": "年份口径说明",
    "inflection_notes": ["拐点与离散波动说明"],
    "claims": [{"claim": "...", "support": "reported", "evidence_ids": ["E001"]}]
  },
  "topic_evolution": {
    "stages": [{"stage": "阶段名", "period": "2018-2021", "basis": "阶段划分依据", "terms": ["..."]}],
    "term_mapping": [{"canonical": "规范词", "variants": ["同义/变体"], "first_year": 2018, "last_year": 2023}],
    "events": [{"type": "emergence", "term": "...", "period": "2019", "cause": "因何变化", "evidence_ids": ["E001"]}],
    "claims": [{"claim": "...", "support": "inferred", "evidence_ids": ["E001"]}]
  },
  "representative_works": {
    "criteria": ["口径说明，如方法首创/里程碑/覆盖阶段，而非仅被引"],
    "stages": [{"stage": "阶段名", "period": "2018-2021",
                "works": [{"doc_id": "D001", "title": "...", "year": 2019, "rationale": "...", "kind": "method"}]}]
  },
  "research_gaps": {
    "gaps": [{"id": "G1", "type": "open_question", "description": "...",
              "exclusion_evidence": "检索式 + 排除条件 + 反例排查", "evidence_ids": ["E001"]}],
    "ruled_out": [{"candidate": "曾被怀疑为空白但被排除", "reason": "排除理由"}]
  },
  "conflicts": [{"items": ["E002", "E003"], "resolution": "未决，或给出分辨率"}],
  "uncertainty": ["归并口径/抽样边界/待定项"],
  "pending_human": ["需人工核验项"],
  "limitations": ["缺失材料/未核验类别/解析失败"]
}
```

填写规则：

- `evidence[].id` 可省略，脚本自动按 `E001` 递增；`quote` 必须是 `/app/work/materials/mat_NNN.txt` 中的**逐字片段**（可先用 `features.json` 的 gap/conflict 候选句，它们本身已是逐字）。
- `evidence[].status` 取 `reported | not_reported | source_conflict | inferred`；`confidence` 取 `high | medium | low`。
- 所有 `evidence_ids` 必须指向 `evidence` 中的既有 id；悬空 id 会被 selftest（S08）拦截。
- `support` 取 `reported | not_reported | inferred`：直接可引用写 `reported`，需归纳写 `inferred`，查不到写 `not_reported` 且不补写。
- `status_hint` 只允许显式降级（`insufficient_evidence`/`input_error`/`partial`）；不要填 `ok`，`ok` 由 compile 自动判定。

## 3. ledger.jsonl 记录

- `{"t":"meta", ...}`：case_id、output_contract、errors、degradations、started_at。
- `{"t":"doc", "doc_id":"D001", "source_file":"materials/mat_001.txt", "title":..., "kind":..., "status":"parsed|failed|skipped", "n_chars":..., "hash":...}`：每篇来源材料一条；`status!=parsed` 的文档不可作为证据载体。
- `{"t":"change", "time":..., "action":"init|extract|compile", "reason":..., "before":..., "after":...}`：流水线每步追加；编译产物 `change_log` 即其投影。

## 4. features.json 的确定性抽取口径

- **年份**：优先级 metadata（case.json 的 documents/sources/papers 列表里的 year/date）> text_header（正文前 1200 字符内出现 ≥2 次的四位年份）。二者皆无则 `year=null, source=missing`，**不猜年份**。`text_header` 来源统一写入 `uncertainty`/`pending_human`，提示人工确认。
- **术语频次**：小写化后按 `[a-z][a-z0-9\-]{2,}` 分词，去停用词，统计一元词（前 60）与二元词组（前 40），记录每 doc 的 count 与首/末出现行。仅作归并依据，**不作为趋势**。
- **空白候选句**：正则命中 `future work / open question / unresolved / limitation / not yet known / remains unclear` 等标记的句子，保留逐字 quote 与行定位。
- **冲突候选句**：正则命中 `however / in contrast / versus / discrepancy / inconsistent` 等标记的句子，保留逐字 quote 与行定位。
- **代表作候选**：语料内每篇解析成功文档即一条候选，`citations=null`（除非材料自带被引，脚本不编造）。

## 5. 主题归并与同名异义

- 先读 `features.json.terms`，把高频词/词组按语义归并为规范词（canonical）与变体（variants），写入 `term_mapping`。
- 同名异义（同一词在不同子领域语义不同）必须在 `variants` 外显式标注，`uncertainty` 写归并口径，`confidence=low`。
- 演化事件 `type` 取值：`emergence`（新出现）、`merge`（合并）、`split`（分裂）、`decline`（衰退）、`terminology_drift`（术语漂移，即“换了说法”）。每类事件必须写 `cause`（因何变化）与 `evidence_ids`。

## 6. 空白判据（区分证据排除与检索遗漏）

- `open_question`：被反复提出但检索范围内无可靠答案，且有排除证据（写了检索式 + 排除条件 + 反例排查）。
- `method_failure`：方法在特定对象/尺度上失效的证据。
- `unresolved_conflict`：冲突证据未决。
- `coverage_absence`：仅能说明“本语料/检索范围内未覆盖”，不能排除其存在；此即检索遗漏，不是真正的空白。
- 每条空白必须写 `exclusion_evidence`；无法写排除证据的只能标 `coverage_absence`。

## 7. 校验顺序（先台账，后编译产物）

`selftest` 依次执行 12 项（S01–S12）：result.json 合法性与 case_id → 固定字段 → output_contract 回填 → 枚举合法 → evidence id 唯一 → **非空 quote 可在来源文本逐字定位（S07）** → claim 的 evidence_ids 可解析（S08）→ 分布计数一致 → status=ok 时的四类完整性 → change_log 含 init → 无伪造分数键。校验顺序保证“坏 quote、悬空引用”在源头即被拦下，而不是事后在 JSON 里打补丁。

## 8. 确定性保证

所有排序固定（按 doc_id/年份/词频降序+词典序），无随机数，时间戳只作元数据不参与决策。同一输入下 `init → extract → compile` 与 `selftest` 的可比较内容一致；`claims.json` 由模型填写，是唯一允许随判断变化的部分。

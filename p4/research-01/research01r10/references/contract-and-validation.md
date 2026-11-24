# result.json 契约与校验顺序（可复用规范）

本文是 `SKILL.md` §3、§5 的展开：说明 `result.json` 的字段语义、受控词表、
契约回填规则与三层校验的具体判定，供 Agent 与人工复核共用。

## 1. 字段语义

| 字段 | 类型 | 语义 |
|---|---|---|
| `case_id` | string | 来自 `case.json` 的 `case_id/caseId/id/armiId`，缺省 `""` |
| `status` | enum | `ok` \| `partial` \| `insufficient_evidence` \| `input_error` |
| `research_product` | object | `type/question/summary/conclusions`，主产物 |
| `evidence_matrix` | array | 逐条证据卡，每行必带 `locator + quote` |
| `uncertainty` | object | `conflicts/biases/gaps/undetermined` 四个列表 |
| `sources` | array | 去重的材料来源定位 |
| `change_log` | array | `{ts, action, detail}` 增量记录 |
| `limitations/errors/degradations` | array | 限制、错误、降级（可读文本） |
| `pending_human` | array | 需人工补齐的具体缺口 |
| `runtime_seconds` | number | 从 skeleton 到 finalize 的墙钟秒数 |

## 2. 受控词表

- **status**：`ok`（结论与证据完整自洽）、`partial`（有产出但缺字段/有引用错误）、
  `insufficient_evidence`（材料不足以成结论）、`input_error`（case.json 不可解析且无材料）。
- **research_product.type**：`synthesis`（综述）、`screening`（系统筛选）、
  `trend`（趋势）、`proposal`（选题评估）。
- **evidence.judgment**：`supports`（支持）、`partial`（部分支持）、`unsupported`
  （不支持/无关）、`conflicts`（与另一条证据冲突）、`extraction_failed`（解析失败）。
- **conclusion.strength**：`established`（材料明确支持）、`inferred`（合理推断）、
  `conjectural`（推测，需明示边界）。

## 3. 契约回填规则（`output_contract` / `output_schema` / `result_schema`）

1. 找到题目契约后，对其每个字段做**物化**：字符串型类型描述（`string/number/array/object/…`）
   替换为对应空值（`""/null/[]/{}`）；具体值原样保留。
2. 物化后的契约与技能内置规范（`CANONICAL`）做**深合并**：契约字段优先，未覆盖的规范字段补齐。
3. 组装时所有契约字段**原样写入** `result.json`，不改名、不删未知键；缺值即为物化空值。
4. 契约之外，强制追加 `status/sources/uncertainty/change_log` 与 `evidence_matrix` 的 `locator+quote`。

## 4. 三层校验顺序（validate-then-write）

**第 1 层 语法**：JSON/JSONL 可解析；证据卡必填 `id/subquestion/source/locator/quote/claim/judgment`
且 `judgment` 在受控词表；结论必填 `id/text/evidence_ids`（非空）且 `strength` 在受控词表；
`uncertainty` 四节均为列表。

**第 2 层 引用**：`conclusion.evidence_ids` 每个 id 必须命中证据卡；`card.conclusion_ids`
每个 id 必须命中结论；重复 id 记警告。子问题 id 与 `decomposition.md` 不一致记警告（不阻断）。

**第 3 层 契约**：§3 规则逐项回填；任何一层失败都写入合法 partial 结果并把原因记入 `errors`。

## 5. 错误分类（`errors` vs `degradations`）

- `errors`：解析失败、必填字段缺失、受控词表越界、悬空引用、写盘失败。
- `degradations`：编码降级、超 2MB 截断、无文本层 PDF（建议 OCR）。
- `limitations`：缺软件/缺权限/范围受限等**已知但非致命**的限制。
- `pending_human`：需要人才能补齐的**具体**缺口（写清缺什么，不写“还需研究”类套话）。

## 6. 确定性要求

同一输入两次运行，除 `runtime_seconds`、`change_log[].ts` 外结果一致：证据 id 固定、
`sort_keys=True` 写 JSONL、字典插入顺序稳定、来源去重有序、子问题解析用固定正则。

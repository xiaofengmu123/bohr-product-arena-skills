# 输入输出契约、证据台账与降级细则

本文档是 SKILL.md 的细则补充，供 Agent 在执行阶段逐条对照。所有脚本路径均相对 Skill 根目录。

## 1. 运行时路径与默认值

| 角色 | 默认路径 | 说明 |
|---|---|---|
| 输入 | `/input` | `case.json`、`task.md` 及 `materials/` 等材料 |
| 工作区 | `/app/work` | 中间态：清单、合同规格、台账、校验报告 |
| 产物 | `/app/output` | 最终稿件与 `result.json`，可被检查 |

脚本全部支持 `--input/--work/--output` 覆盖默认值；缺参时不报错，直接落到上述默认目录。路径用相对形式调用：`python3 -B scripts/main.py discover --input /input --work /app/work --output /app/output`。

## 2. 输入发现规则（先发现，再假设）

1. 扫描输入目录，不假设固定文件名：按扩展名/文件名识别 `case.json`、`task.md`、PDF、`.bib`、`.md`、表格、图片、Office 文档等；目录按字典序、文件按字典序输出，保证确定性。
2. 单材料 >2MB 跳过，记入 `degradations`（`issue=too_large`），不阻断其余材料。
3. `case.json` 用 `utf-8-sig→utf-8→gb18030→latin-1` 顺序解码；坏 JSON 记录 `case_error`，不抛异常，其余材料继续处理。
4. 材料内容一律按实际文件读取，不依据文件名标签臆测内容；PDF 用平台提供的解析工具提取，不使用预提取答案层。

## 3. output_contract 发现与回填

- 合同字段按优先级从 case.json 递归查找：`output_contract → output → expected_output → fields → required_fields → result_fields → contract → output_schema`；先查顶层，再查嵌套（深度 ≤6）。
- 归一化为 `[{name, kind}]`，`kind ∈ str|list|dict|unknown`：值为对象→`dict`（回填 `{}`）、值为数组→`list`（回填 `[]`）、其余→`str`/`unknown`（回填 `null`）。
- **回填规则**：字段名原样复制为 result.json 顶层键，不改名、不删键；未产出/未知值填 `null` 或 `[]`。合同字段名与标准字段名冲突时，合同字段优先；`case_id` 与 `status` 例外，始终写入真实值。
- `discover` 会把归一化结果写入 `work/contract_spec.json`；Agent 必须核对 `work/case_normalized.json`，脚本抽取不全的合同字段由 Agent 手工补进 result.json。

## 4. 证据台账（work/ledger.json，唯一中间态）

与“facts.md + evidence_map.json + change_log.json 三处手工维护”的常见做法不同，本 Skill 用**一份台账**做唯一事实源，`evidence_map.json`、`change_log.json` 与 `preserved_facts.json` 由 `verify` 从台账**派生**，避免两份记录漂移。

```json
{
  "schema_version": 1,
  "case_id": "case-x",
  "claims": [
    {
      "id": "C001",
      "section": "background|related_work|gap|contribution",
      "claim": "转述后的论断（按案例语言）",
      "quote": "材料逐字片段",
      "locator": "materials/paper.md#p3",
      "kind": "fact|citation|number|method|limitation|contribution",
      "source_ref": "S1",
      "status": "supported|gap|conflict|author_judgment|not_reported",
      "used_in": ["manuscript.md"]
    }
  ],
  "sources": [
    {"key": "S1", "locator": "materials/paper.md#p3", "quote": "材料逐字片段",
     "kind": "paper|bib|note|case", "status": "supported|source_conflict"}
  ],
  "changes": [
    {"at": "draft", "target": "manuscript.md", "before": null, "after": "改写后的句子", "reason": "按四段重排"}
  ],
  "conflicts": [
    {"metric": "精度", "records": [{"locator": "...", "quote": "...", "value": "92.3%"},
                                  {"locator": "...", "quote": "...", "value": "88.1%"}]}
  ]
}
```

字段约定：

- `claims`：每条事实/数字/引用/贡献一个条目。`quote` 必须是材料逐字片段（不缩写）；`locator` 形如 `materials/paper.md#p3`（无页码信息可写文件级定位）；`status` 为 `author_judgment` 表示作者判断、`not_reported` 表示材料未报告、`conflict` 表示多来源不一致。
- `sources`：正文 `[S#]` 锚点指向的源记录。`key` 形如 `S1`、`S2`；`quote` 与 `locator` 必填（缺失会被 verify 标 `source_missing_quote/locator`）。
- `conflicts`：同一指标两来源不一致时**两条都保留**并标 `source_conflict`，不隐藏、不择一。
- `changes`：改写/组装记录，可回退；数值与单位的“保全清单”并入 `claims`（`kind=number` 且 `quote` 为原值）。

## 5. result.json 标准字段

`emit_skeleton` 生成、Agent 增量回填；`verify` 校验存在性。

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | 任意 | 来自 case.json，缺失为 `null` 并标 warning |
| `status` | str | `ok | partial | insufficient_evidence | input_error` |
| `sources` | list | `{key, locator, quote, kind, status}` |
| `uncertainty` | list | `{item, reason}`：材料不足/冲突/未证实处 |
| `change_log` | list | `{at, target, before, after, reason}` |
| `artifacts` | object | 主产物与佐证文件名 → 路径 |
| `pending_human` | list | 需要人工确认的项 |
| `errors` | list | 输入/运行错误 |
| `degradations` | list | 降级处置记录 |
| `runtime` | object | 预算消耗（由 Agent 在收尾回填） |
| `contract_fields` | list | 已回填的合同字段名（用于自查） |

**status 判定**：四段齐全 + 证据齐全 → `ok`；有可用稿但存缺口 → `partial`；材料不足以成稿 → `insufficient_evidence`；case.json 无法解析 → `input_error`。

## 6. 校验顺序（合同先行，内容其次）

`verify` 的判定顺序固定，便于分支：

1. result.json 能否解析、是否为对象（不可读 → 退出码 2）；
2. 合同字段是否全量回填（缺任一 → `errors`，硬伤）；
3. `status` 是否合法、`case_id` 是否存在（软提醒）；
4. 稿件四段（研究背景/相关工作/研究空白/贡献）是否齐全、`[S#]` 锚点是否都能在台账 `sources` 中解析；
5. 台账来源是否带 `quote` + `locator`。

退出码：`0` 无硬伤；`2` 输入/结果不可解析；`3` 存在硬伤（缺合同字段、status 非法）。warnings 不改变退出码，但要求 Agent 逐条处理或在 `uncertainty` 中说明。

## 7. 降级处置表

| 场景 | 处置 |
|---|---|
| 材料不足 / 文献库为空 | `insufficient_evidence`；正文只写可支撑内容，其余标 `MATERIAL GAP`，不示例填充 |
| 引用查不到原文 | `pending_human` + `uncertainty`，不臆造元数据（作者/年份/DOI） |
| 目标期刊格式未知 | 作者—年份 + `uncertainty` 声明假设，不冒充特定格式 |
| 语言切换 | 术语/数字/引用原位保留，仅换语，限定强度不变 |
| PDF/软件不可用 | `degradations` 记录，用现有文本继续，不伪造缺失状态 |
| 写盘失败 | 最小合法 result.json（`input_error` + `errors`）收尾，不让进程崩溃 |

## 8. 确定性要求

- `emit_skeleton` 不含墙钟时间、键序固定（`sort_keys=True`）；`scan_input` 目录与文件按字典序；同一输入两次运行字节一致。
- 墙钟/请求计数只记录在 `work/` 侧或收尾时由 Agent 回填 `runtime`，不进入骨架，因此骨架可复现。
- 随机成分（如有）必须固定种子；本 Skill 当前实现无随机成分。

## 9. 边界

不做期刊排版模板适配；不生成材料外事实；不自动下载或联网补参考文献；不编造 DOI、作者、年份、评测分数。一个 Skill 只解决“引言与相关工作”一类任务，不扩展到整篇论文排版。

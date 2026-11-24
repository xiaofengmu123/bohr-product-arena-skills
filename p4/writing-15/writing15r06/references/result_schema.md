# result.json 结构与证据映射规范

本文档说明 Skill 运行后每个案例必须产出的 `/app/output/result.json` 结构，供 Agent 在生成与终检时对照。

## 1. 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 从 case.json / task.md 抽取的案例标识，无法确定时用文件名代替并注明 |
| `status` | string | `ok` / `partial` / `insufficient_evidence` / `input_error`，必须诚实 |
| `<output_contract 各字段>` | 各类型 | 题目 output_contract 的全部键原样回填，不改名、不删未知键；缺值给 `null`/`[]` |
| `sources` | list | 输入材料与文献来源清单 |
| `uncertainty` | list | 不确定点、待人工确认项 |
| `change_log` | list | 修改记录（章节级改动、为何改、影响范围） |
| `errors` | list | 运行中可恢复的错误 |
| `degradations` | list | 降级处理（坏编码、超长截断、缺软件等） |
| `artifacts` | object | 主产物与预览文件的相对/绝对路径 |
| `pending_human` | list | 必须人工确认才可定稿的项 |
| `runtime` | object | 起止时间、阶段、耗时 |

## 2. 状态枚举语义

- `ok`：主产物完成且关键论断均有来源定位，无必填缺口。
- `partial`：有可用产物但存在已知缺口，`pending_human`/`uncertainty` 非空。
- `insufficient_evidence`：材料不足，无法可靠生成结论；只交付缺口清单，不编造。
- `input_error`：输入无法解析（坏 JSON、缺 case.json 等）且不可恢复。

## 3. sources 条目

```json
{"id": "s1", "file": "materials/paper.md", "locator": "materials/paper.md#p3",
 "quote": "逐字引用原文片段", "role": "fact|citation|term"}
```

`locator` 采用 `文件路径#定位` 形式；`quote` 必须逐字取自材料，禁止改写。

## 4. evidence 映射（可核验性）

每个关键结论/数值/术语/引用至少一条：

```json
{"claim": "结论或数值陈述", "source_id": "s1",
 "locator": "materials/paper.md#p3", "quote": "……"}
```

同一指标多来源冲突时，两条都记录并标 `source_conflict`。

## 5. change_log 条目

```json
{"time": "ISO8601", "scope": "章节/段落定位", "action": "新增|改写|删除|纠错",
 "reason": "依据来源或评审意见", "diff": "改动前后要点", "reversible": true}
```

## 6. 不变量

- result.json 必须 JSON 合法；中途异常也先写 partial 骨架再改。
- 不新增材料外事实；找不到原文时写 `not_reported`，不写占位文献。
- 可编辑产物（.md/.docx/.pptx/.xlsx）若出现，应配套真实 PDF/PNG 预览路径写入 `artifacts`。

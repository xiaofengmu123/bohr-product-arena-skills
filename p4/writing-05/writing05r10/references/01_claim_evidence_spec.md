# 论断—证据中间表示（IR）规范

本文件定义 `claim_graph.json`（工作目录 `/app/work/claim_graph.json`）的结构与
校验规则。它是本 Skill 与参考结构诊断类方案的核心差异点：**先建图、先校验、后渲染**。
最终 `result.json` 与 `evidence_writing.md` 都由这份经过校验的 IR 派生，而不是先写
成稿再回头补证据。

## 1. 顶层结构

```json
{
  "schema_version": "1.0.0",
  "case_id": "取自 case.json",
  "materials": [{"id": "M1", "path": "原文路径", "kind": "pdf|txt|md", "title": "可选"}],
  "claims": [],
  "sources": [],
  "draft": "",
  "summary": "",
  "uncertainty": [],
  "pending_human": [],
  "limitations": []
}
```

- `claims` 为论断清单：把稿件拆成**可独立判定的实质论断单元**（一句承载一个可
  真可假的主张），不把纯背景陈述、目录句、过渡句当成论断。
- `sources` 为证据表来源：每条来源对应材料中的具体段落，含逐字摘录与定位。
- `draft` 为成稿/改稿正文（或指向可编辑产物路径的说明）。

## 2. 论断（claim）字段

| 字段 | 必填 | 语义 |
|---|---|---|
| `id` | 是 | `C001`、`C002`… 确定性编号 |
| `statement` | 是 | 该论断的一句话表述（可来自原文或改后句） |
| `locator` | 是 | `S{节}.P{段}.s{句}`，无节时 `P{段}` 或字符区间 |
| `quote` | 是 | 原文**逐字**摘录，须能被 `verify_quote` 复核 |
| `source_ids` | 是 | 支撑来源的 `S001` 列表；可为空但须写 `missing_source` |
| `support` | 是 | `direct`（原文直接支持）/ `indirect`（相近但更弱或更强）/ `none`（无支撑）/ `conflict`（多源冲突） |
| `strength` | 是 | 证据强度等级，取值见 `references/02_strength_and_uncertainty.md` |
| `status` | 是 | `ok` / `needs_edit` / `missing_source` / `overclaim` / `conflict` |
| `uncertainty` | 否 | 原文是否已恰当限定、需如何改写，防止相关说成因果、单篇说成共识 |
| `action` | 否 | 具体编辑动作（收窄、补限定、补引用、删改写），不是通用原则 |

### 支撑范围判定（对得上）

- `direct`：引用段落与论断表述**同义**，且原文确实这样写。
- `indirect`：原文支持一个相近但**更弱或更强**的主张；此时必须改写论断或降级 strength。
- `none`：找不到支撑；写入 `pending_human` 或 `missing_source`。
- `conflict`：多来源结论相反；不得只保留一个，需记录两方并标注。

## 3. 来源（source）字段

| 字段 | 必填 | 语义 |
|---|---|---|
| `id` | 是 | `S001`… |
| `type` | 是 | `original`（原始研究）/ `review`（综述）/ `guideline`（指南）/ `preprint` / `secondary`（二手转引）/ `unknown` |
| `citation` | 是 | 作者 年份 期刊/会议，或可回溯的引用字符串 |
| `locator` | 是 | 与论断同格式；指向该来源材料中的段落 |
| `quote` | 是 | 原文逐字摘录 |
| `metadata_status` | 是 | `verified`（真实性/著录已核）/ `unverified`（未核）/ `not_reported` |
| `source_doc` | 是 | 材料文件名，便于回溯 |
| `note` | 否 | 二手转引、撤稿、版本差异等核验路径说明 |

## 4. 校验顺序（校验前置）

渲染前依次执行，任一步不过则写回 `issues`/`warnings`：

1. `id` 唯一；`statement`/`quote`/`locator` 非空。
2. `support`、`strength`、`status`、`type` 取值合法。
3. 每条 `source_ids` 都能在 `sources` 中解析。
4. 每条 `quote` 能被 `verify_quote` 对材料原文复核为逐字（无法自动核验时记
   `metadata_status=unverified`，不冒充已核验）。
5. 因果动词 + `indirect/none/conflict` 支撑时触发 overclaim 预警。

`main.py --finalize` 调用 `core_impl.validate_claim_graph` 完成上述检查；有 error
则 `status=partial`，无材料则 `status=insufficient_evidence`。

## 5. 定位符（locator）

统一 `S{section}.P{para}.s{sentence}`：
- 有章节编号用 `S2.P3.s1`（第 2 节第 3 段第 1 句）。
- 无章节用 `P3.s2`；段落也不清晰用字符区间 `chars:120-180`。
- 每条判断必须能凭 locator + quote 指回原文复核，不新增材料外事实。

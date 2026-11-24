# 归一化、去重、版本链与不确定性规则

本文件是 `scripts/core_impl.py` 中受控词表的规范说明，与代码保持一致。运行前先读；任何与代码不一致之处以代码为准（代码可执行、可自检）。

## 1. 去重键（canonical key）

稳定键优先级：**DOI > arXiv > 归一标题**。同一条记录同时有 DOI 与 arXiv 时取 DOI；预印本与正式版通过「归一标题」归并，但仅在标题归一后完全一致时才视为同一工作。

| 键类型 | 归一规则 | 示例 |
|-|-|-|
| DOI | 小写；去 `https://doi.org/`、`http://dx.doi.org/`、`doi:` 前缀；NFKD 去重音；折叠空白 | `10.1000/xyz` |
| arXiv | 提取 `\d{4}\.\d{4,5}`；去掉 `vN` 版本号，使预印本与修订版同键 | `2412.00001` |
| 标题 | NFKD 去重音；小写；非字母数字/汉字改为空格；折叠空白 | `deep learning for science` |

去重是确定性的：相同记录集合两次运行得到相同 `clusters` 与 `dropped`，主记录标注 `_suppressed_duplicates` 计数。无法识别任何键的记录标 `_dedup_status=unidentifiable`，保留而不丢弃，并在 `uncertainty` 中说明。

## 2. 版本链与阶段标签（保守）

`build_version_chain` 只按**显式信号**给阶段标签，绝不臆测预印本与 DOI 是同一篇：

- `retraction`：`retracted=true`，或 title/type 含 `retraction`。
- `correction`：type 为 `erratum`/`correction`/`corrigendum`。
- `preprint`：有 arXiv 且无 DOI/期刊。
- `published`：有 DOI。
- 否则 `unclassified`（不猜）。

版本链输出是「合并骨架」，供 Agent 用材料中的逐字证据覆盖；脚本自身不断言跨阶段同一性。

## 3. 时间窗

- 闭区间、连续无缝，`validate_window` 强制 `start <= end`。
- 一律 UTC 归一；无时区的裸时间按 UTC 处理并记 warning。
- 跨窗边界与延迟入库条目：凡落入当前闭区间者都纳入；上一快照已见的 canonical key 判 `known`（抑制推送），未见判 `new`。

## 4. 增量判定

`classify_incremental(records, snapshot_keys)` 相对上一快照把记录分为 `new / updated / removed / retractions / known`。撤稿从 `new` 中分离；`known` 不重复推送。快照存上一窗口的 canonical key 集合（`snapshot.canonical_ids`），构成增量水位。

## 5. 证据置信度（evidence confidence）

每个关键结论必须带 `confidence`，受控取值：

| 取值 | 含义 | quote 要求 |
|-|-|-|
| `verbatim` | 逐字证据 | 必须非空，来自材料原文 |
| `paraphrased` | 转述 | 可空，`basis` 必填 |
| `inferred` | 推断 | 可空，`basis` 说明推导 |
| `unreported` | 未报告 | 空，明确标注缺失 |
| `conflict` | 冲突 | 两个来源都记录，标 `source_conflict` |

`make_evidence_item` 会把空的 `verbatim` 降级为 `paraphrased`，保证「逐字引用」标签不被滥用。

## 6. 不确定性与限制（本变体重点）

**uncertainty**：无法判定/相互冲突的问题点。字段 `severity ∈ {blocking, material, minor}`、`resolution ∈ {pending_human, accepted_with_caveat, unresolvable}`、`affects` 列出受影响的字段路径。冲突信息两者都记，不隐藏。

**limitations**：能力/材料/工具的客观边界，`reason` 受控取值：

- `no_material` 输入材料缺失或为空
- `tool_missing` 需要的软件/工具不可用
- `time_exceeded` 预算内未完成
- `permission` 权限/接口拒绝
- `out_of_scope` 超出本 Skill 职责
- `encoding` 坏编码/坏 JSON
- `size_cap` 超单文件上限被跳过
- `parse_failed` 可读但无法解析

未知 reason 会被安全收编为 `parse_failed`，不丢信息。**缺数据不编造**：任何材料未给出的数值、DOI、日期一律写 `not_reported`/`unreported`，并落到 `pending_human` 或 `limitations`，绝不补造。

## 7. 幂等与可回放

- 全部脚本函数确定：相同输入 → 相同输出；无随机种子、无当前时间以外的漂移源（`change_log` 的时间戳除外，指纹计算不含它）。
- `fingerprint(obj)` 对排序键的规范化 JSON 求 SHA-256，用于核验「同快照 + 同窗口 + 同来源 → 同结果」。
- 重跑不二次推送：`known` 条目与 `dropped` 重复条目被抑制。

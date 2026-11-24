# 确定性与可复跑规则（references/determinism-rules.md）

本 Skill 把"同输入两次产物一致"作为显式设计目标。规则如下：

## 保证边界

- **脚本层保证**：相同输入文件 + 相同 `/app/work/` 工作笔记（decomposition.md、search_plan.md、evidence_cards.jsonl、payload.json）两次组装，`result.json` 字节一致。
- **非脚本层**：模型每次自行生成的问题拆解、检索式与证据内容可能不同；这属于研究工作本身的差异，不在确定性范围内。要复现，需固定 `/app/work/` 笔记后重跑 `finalize`。

## 具体规则

1. `result.json` 不写入墙钟时间：`change_log` 用自增 `seq`，不用 ISO 时间戳。
2. 墙钟/主机/进程/运行时长写入 `/app/work/run_metadata.json`，与结果内容分离。
3. 所有 JSON 用 `json.dumps(..., sort_keys=True, ensure_ascii=False)` 序列化，末尾一个换行。
4. `finalize` 做规范排序：
   - `evidence_matrix` 按 `source → locator → quote` 排序，证据 id 稳定重编号为 `E1..En`；
   - 结论里的 `evidence_ids` 按新旧 id 映射同步改写；
   - `sources` 与 `uncertainty.{conflicts,biases,gaps,undetermined}` 按规范化 JSON 排序。
5. `skeleton` 幂等：结果文件已存在时不覆盖、不重建，只补齐缺失契约键。
6. 读取顺序稳定：`discover_input` 对目录与文件名排序，遍历结果确定。

## 验证方法

```bash
python3 -B scripts/main.py selftest
```

其中 `determinism` 检查项用同一份 demo 输入与 payload 跑两遍流水线，比较 `result.json` 字节。

# 验收口径与 result.json 字段规范

本文件供交付前自检与评审复用。逐条对应评分维度，全部满足才允许把 `status` 改为 `ok`。

## 1. result.json 字段规范

```json
{
  "case_id": "字符串，来自输入；找不到为 unknown",
  "<output_contract 的每个字段>": "按契约类型给值；缺值 null/[] 并在 uncertainty 说明",
  "status": "ok | partial | insufficient_evidence | input_error",
  "sources": [{"path": "...", "kind": "pdf|text|case|task", "bytes": 0, "parsed": "pdftotext|utf-8"}],
  "uncertainty": ["材料缺口/冲突/假设/未确认项，逐条列出"],
  "change_log": [{"ts": "...", "step": "...", "action": "...", "status": "..."}],
  "errors": ["运行错误"],
  "degradations": ["降级处理，如 pdf_text_unavailable、case_json:bad-json"],
  "pending_human": ["需人工确认项"]
}
```

## 2. 状态判定口径

| 情况 | status |
|-|-|
| 完成全部要求、自检通过 | `ok` |
| 完成但存在材料缺口/部分字段待确认 | `partial` |
| 源材料不足，无法成稿 | `insufficient_evidence` |
| 输入无法解析或契约缺失导致无法运行 | `input_error` |

**禁止**：材料不足却写 `ok`；用 `partial` 掩盖编造。

## 3. 运行前自检（提交 Skill 前，本地执行）

```bash
python3 -B scripts/scaffold.py --help            # 退出码 0
python3 -B scripts/facts.py --help
python3 -B scripts/check.py --help
# 用一份最小假输入跑一遍（空目录、坏 JSON、正常各一次）
python3 -B scripts/scaffold.py --input ./demo/empty --output ./demo/out --work ./demo/work
python3 - <<'PY'
import json, pathlib
r = json.load(open('./demo/out/result.json', encoding='utf-8'))
assert r.get('case_id') and r.get('status') in {'ok','partial','insufficient_evidence','input_error'}
print('ok', r['status'])
PY
```

要求：空目录退出码 0 且 `status=insufficient_evidence`；坏 JSON 记录到 `errors/degradations` 不崩溃；正常输入产出骨架且两次运行结果一致（除时间戳外内容一致）。

## 4. 交付前逐条勾选（映射评分维度）

实际效果（40 分）

- [ ] 成稿满足指定体裁/受众/篇幅，可直接使用。
- [ ] 事实、数据、单位、图表编号、引用、论断强度与源文一致，无新增结论。
- [ ] 关键结论、数据、限定条件、方法要点、图表引用全部保留。
- [ ] 提供逐段对照、改动清单、删改理由、待确认问题。

使用增益（25 分）

- [ ] 同一源文+读者+体裁+篇幅下，事实漂移/受众错配/术语不一致少于基础 Agent（可对照证据映射记录）。
- [ ] 改动清单减少返工轮次与核对成本。
- [ ] 同源文对照、同评分表、参考稿等公平证据齐全。

创新增益（15 分）

- [ ] 形成“受众画像→取舍策略→边界标注→多文体映射”链条（本 Skill 第 4 节 + rewrite-rules.md）。
- [ ] 保留优先级、术语锁定、论断强度分级、逐段对照真实落地。
- [ ] 能说明相对基础 Agent/通用工具/人工流程的边界与价值。

可靠体验（10 分）

- [ ] “源文+读者+体裁”即可正确触发，说明完整。
- [ ] 同源文重复运行、长文分段、跨文体切换均稳定。
- [ ] 缺引用、数据自相矛盾、体裁与篇幅冲突、涉密/未发表均被识别与处置。

生态贡献（10 分）

- [ ] SKILL.md、模板、术语表、验收口径、失败处理、版本维护信息齐全。
- [ ] 术语表、必须保留项清单、体裁模板、改动清单格式、题集可复用。
- [ ] 沉淀了可分享的案例/题集/改写方法与真实价值。

## 5. 通用失败处理

| 场景 | 处置 |
|-|-|
| 空输入目录 | 退出码 0，`insufficient_evidence` |
| 坏 JSON / 坏编码 | 记 `errors/degradations`，继续处理其余材料 |
| 缺 `--input/--output/--work` | 默认 `/input`、`/app/output`、`/app/work` |
| 写盘失败 | 降到最小合法产物，不崩溃 |
| PDF 无文本层 | 尝试 pdftotext→同名文本→记 `pdf_text_unavailable`，不编造内容 |
| 数字/引用冲突 | 双源都记 + `source_conflict` |

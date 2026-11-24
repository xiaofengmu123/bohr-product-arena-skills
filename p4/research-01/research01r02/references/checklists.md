# 深度研究 Skill 规则与检查清单

本文是 `SKILL.md` 的补充细则，Agent 在执行各阶段时按此判质量；评审可按此复核。

## 1. 问题拆解质量（P1）

- 子问题编号用 `SQ1..SQn`，每个 SQ 必须能被一个独立检索动作回答，不能是原句换关键词。
- 每个 SQ 标注依赖边：`前置=SQ0/SQ2` 或 `可并行`；禁止让多个 SQ 无差别重复查同一批文献。
- 对"研究对象、机制、方法、时间范围、比较关系、证据要求"六要素逐项提取，缺的要素写"未限定"。
- 合格示例：`SQ1：X 干预在 RCT 中的主要终点效应量（前置=无，可并行）`。
- 不合格示例：`SQ1：研究 X 干预`（没有明确要取回什么字段）。

## 2. 检索计划规则（P1）

写清四点，缺一不可：

1. 顺序：哪些 SQ 先查、哪些依赖前序结果才能定检索式。
2. 来源范围：限定到 `/input` 的具体材料/目录或允许的外部检索（注明检索式）。
3. 停止条件：每条结论已有逐字定位，或出现显式缺口，或追加检索边际收益为 0。
4. 去重：同一检索式/同一批文献不重复执行，命中同一材料时复用已有证据卡。

## 3. 证据卡质量（P2）

每行写入 `/app/work/evidence_cards.jsonl`，一 JSON 一行，字段与 `result.json` 的 `evidence_matrix` 一致：

```json
{"id":"E1","subquestion":"SQ1","source":"materials/a.pdf","locator":"p3 §2.2",
 "quote":"逐字引用","claim":"支持的论断","judgment":"supports","reason":"理由",
 "conclusion_ids":["C1"],"trace":{"tool":"pdf_text","params":{},"outcome":"ok"}}
```

- `quote` 必须逐字取自材料，禁止改写或摘要；`locator` 要能定位到段落/行/页/节。
- `judgment` 只取 `supports / partial / unsupported / conflicts / extraction_failed`。
- 无法提取文本的 PDF/图 → `judgment=extraction_failed`，`reason` 写"无文本层，需 OCR"。
- `trace` 记录本次工具调用的参数与结果，缺了就无法复跑。

## 4. 冲突证据规则（P3）

- 同一指标两个来源不一致：**两个都保留**，写进 `uncertainty.conflicts`，每条含 `sources`（≥2）、`claim`、`resolution`（或 `status:"undetermined"`）。
- 禁止静默择一、禁止只写"尚存争议"。
- 能裁定的写明裁定依据（材料内逐字值 > 摘要 > 记忆）；不能裁定的标 `undetermined`。

## 5. 研究空白规则（P3）

- `uncertainty.gaps` / `pending_human` 只写**具体缺口**：缺哪个字段、缺哪类材料、为什么缺、影响哪条结论。
- 禁止套话："尚存争议""需要更多研究""证据有限"。
- 合格示例：`{"what":"未报告样本量与 95%CI","why":"材料只给点估计","affects":"C1 的精度表述"}`。

## 6. 状态与强度约定

| status | 何时使用 |
|---|---|
| `ok` | 研究问题有结论、关键判断均有逐字证据回链、冲突已并列、缺口已显式 |
| `partial` | 走完流程但存在部分缺口或失败提取 |
| `insufficient_evidence` | 材料缺失/不足以支撑结论，`pending_human` 列出缺口 |
| `input_error` | case.json 不可解析等输入层错误 |

- 结论强度 `established`（材料内逐字支持）> `inferred`（由多条材料合理推断）> `conjectural`（仅有间接或弱证据）。
- 强度与判定必须一致：`judgment=partial/unsupported` 的结论不得标 `established`。

## 7. 预算纪律（贯穿全程）

- 阶段截止时刻由 `run_state.json` 的 `deadlines` 给出；每次 `checkpoint` 输出 `remaining_seconds / remaining_requests / late`。
- `late=true` 或 `remaining_requests<=4` 时：停止新增检索，用已有证据合成结论，缺口入 `pending_human`。
- P0 之后 `checkpoint` 若报 `p0_ok=false`，说明骨架缺失，立即补 `skeleton`，不要继续检索。

## 8. 确定性

- 证据 id 固定（E1..En 或自定 id），`merge`/`finalize` 只追加去重，不重排、不覆盖。
- 同一输入两次运行：证据 id、结论、证据矩阵顺序一致；`runtime_seconds` 与 trace 时间戳为运行元数据，允许不同。

## 9. 交付前自检（开发机）

```bash
python3 -B scripts/main.py --help
python3 -B scripts/selftest.py        # 全链路 + 空输入 + 坏 JSON + 确定性
python3 - <<'PY'
import json
r = json.load(open('/app/output/result.json', encoding='utf-8'))
assert isinstance(r, dict) and 'case_id' in r and 'status' in r
print('ok', r.get('status'))
PY
```

三个场景各跑一次：正常 demo、空输入目录、坏 JSON case.json；三者都必须退出码 0 并写合法 result.json。

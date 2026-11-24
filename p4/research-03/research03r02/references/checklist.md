# 交付前检查清单与最小调用示例

本文件把评分维度翻译成可逐条执行的操作项，提交/交付前按序跑一遍；`scripts/selftest.py` 是对同一契约的自动验证（fixture 均标注“合成示例”，非正式案例、非预制答案）。

## 1. 交付前逐条检查

**实际效果（40 分）**

- [ ] 问题拆解：`review_question` 非空；`subquestions` ≥1（推荐 ≥2）且用 `depends_on` 标依赖。
- [ ] 检索式：每个 `query_blocks` 含 `database/query/intent/time_limit|language`，可执行、覆盖同义词与受控词。
- [ ] 纳排标准：条款化、可判定、含 `priority`；`protocol.json` 落盘时间早于 `screening.jsonl` 判定时间。
- [ ] 筛选记录：题录+全文两轮逐条 `decision`；每条 exclude 有 `reason+criterion_id+judge`。
- [ ] 质量评价：逐维度 `rating+basis_locator+quote` + `overall` + `impact`。
- [ ] 证据回链：每条结论 `evidence_ids` 命中 `evidence_map`，每行 `locator+quote` 逐字取自材料。
- [ ] 冲突与空白：`conflicts` 逐条 ≥2 来源；`gaps` 落到具体缺口。
- [ ] 综合结论：`conclusions` 非空、与研究问题一致、不越过证据范围。

**可靠体验（10 分）**

- [ ] `python -B scripts/main.py selfcheck` 通过（`ok=true` 或仅有 warning）。
- [ ] result.json 可被 `json.load` 解析；`case_id`、`status`、诚实字段齐全。
- [ ] 空输入/坏 JSON/坏编码/检索为空/全文不可得/条款冲突/证据矛盾均记录且退出码 0。
- [ ] 同一输入重复运行结果一致。

**生态贡献（10 分）**

- [ ] 检索式模板、纳排标准、筛选表、证据表、质量量表在 `references/protocol.md` 有可读定义，可被他人复用。

## 2. 运行时顺序

```bash
python -B scripts/main.py discover
python -B scripts/main.py skeleton          # 3 分钟内必须完成，落盘 partial
python -B scripts/main.py budget            # 每次实质请求前查看 verdict
# ...写 /app/work/protocol.json、screening.jsonl、quality_appraisal.jsonl、
#    evidence_cards.jsonl、synthesis.json...
python -B scripts/main.py budget tick       # 每次模型回复后记账
python -B scripts/main.py commit --payload /app/work/protocol.json --phase P1
python -B scripts/main.py commit --field synthesis --file /app/work/synthesis.json
python -B scripts/main.py finalize
python -B scripts/main.py selfcheck
```

`budget` 的 `verdict` 决定下一步：

- `on_track`：继续当前阶段。
- `behind`：当前阶段最小收口，立即 `finalize`，不扩展范围。
- `critical`（剩余 ≤120s 或请求 ≤4 次）：停止新内容工作，立即 `finalize`。
- `stop`（≥840s 或请求耗尽）：只写盘，不再发新模型请求。

## 3. 最小调用示例（示意输出，非正式案例）

```bash
python -B scripts/main.py discover
# => {"input_dir":"/input","exists":true,"case_json":"/input/case.json",...}

python -B scripts/main.py skeleton
# => {"case_id":"challenge-51977f9b","wrote":true,...}

python -B scripts/main.py budget
# => {"verdict":"on_track","phase":"P0","remaining_s":900,"requests_remaining":64,...}

python -B scripts/main.py finalize
# => {"ok":true,"status":"partial","artifacts":["result.json","review.md",...]}

python -B scripts/main.py selfcheck
# => {"ok":false,"errors":[],"warnings":["subquestions empty"],...}
```

示意输出仅为说明接口与流程，不冒充正式案例结果。

## 4. 自检脚本

```bash
python -B scripts/selftest.py
```

覆盖：happy path（完整五环节）、空输入、坏 JSON、缺 case.json、未知 contract 字段五条路径；退出码 0 表示全部通过。脚本只依赖标准库，用 `tempfile` 在临时目录生成合成示例输入，不写正式产物、不预置测试答案。

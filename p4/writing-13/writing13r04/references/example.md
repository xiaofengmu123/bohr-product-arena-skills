# 最小调用示例（示意，非正式案例）

> 以下为**合成示例**，仅用于说明接口与七要素—证据—已完成/拟开展的对应方法，**不冒充任何正式案例结果**。

## 1. 输入

`/input/case.json`（示意）：

```json
{"id": "demo-001",
 "output_contract": {"artifact": {}, "fact_source_map": [], "version_boundary": {}},
 "task": "为一篇关于X机制的前期论文，撰写'创新点'与'研究基础与可行性'两节初稿，区分已完成与拟开展。"}
```

`/input/paper.txt`（示意材料，片段）：`We improved the sensitivity of X detection to 0.1 nM (Fig. 2).`

## 2. 调用

```bash
python3 scripts/main.py --case /input/case.json --out /app/output --work /app/work
python3 scripts/ledger.py add --element 创新点 --status paraphrased --tense done \
  --claim "已有工作将X检测灵敏度提升至0.1 nM" --locator "paper.txt:Fig.2" \
  --quote "improved the sensitivity of X detection to 0.1 nM"
python3 scripts/ledger.py add --element 创新点 --status not_reported --tense unknown \
  --claim "申请人在该方向的前期积累" --note "材料未提供申请人自己的前期成果，正文标待补充"
python3 scripts/ledger.py verify
python3 scripts/ledger.py export --out /app/work/evidence_map.json
python3 scripts/selftest.py --case /input/case.json --out /app/output
```

## 3. 示意输出

`result.json`（关键字段示意）：

```json
{
  "case_id": "demo-001",
  "status": "partial",
  "artifact": {"path": "/app/output/draft.md", "kind": "grant_section_draft"},
  "fact_source_map": [
    {"id": "c001", "element": "创新点", "ev_status": "paraphrased",
     "tense": "done", "locator": "paper.txt:Fig.2",
     "quote": "improved the sensitivity of X detection to 0.1 nM"},
    {"id": "c002", "element": "创新点", "ev_status": "not_reported",
     "tense": "unknown", "quote": "",
     "note": "申请人前期积累缺失，正文标【材料未提供，待申请人补充】"}
  ],
  "version_boundary": {"material_gaps": ["申请人前期成果证据缺失"], "anomalies": []},
  "uncertainty": ["c002 无法判断申请人前期能力"],
  "pending_human": ["申请人需补充前期成果清单"]
}
```

`draft.md` 中对应正文写法（示意）：

> 已有工作已将 X 检测灵敏度提升至 0.1 nM（paper.txt:Fig.2）。申请人前期在该方向的研究基础【材料未提供，待申请人补充】。

## 4. 判定要点

- `c001` 为 `paraphrased`：有 locator＋quote，可回溯；
- `c002` 为 `not_reported`：quote 为空，正文用显式占位符，不编造成果；
- `tense=done` 只用于材料已有事实；申请人自己的积累缺失时用 `not_reported`＋`pending_human`。

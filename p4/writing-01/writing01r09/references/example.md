# 最小调用示例（示例，非正式案例结果）

> 以下输入与输出均为**示意**，用于说明接口与完整流程，不冒充任何正式案例的答案。

## 输入示例 `/input/case.json`

```json
{
  "case_id": "example-001",
  "task": "基于给定实验记录写一段 250 字的结果段",
  "output_contract": {
    "deliverable": "manuscript.md",
    "fact_source_map": "evidence_map.json",
    "version_limits": "version_limits.json"
  }
}
```

`/input/task.md` 为题目说明；`/input/notes.md` 为实验记录材料（示意）。

## 调用步骤

```bash
# Step 0：前 3 分钟内落盘骨架
python3 scripts/main.py skeleton --input /input --output /app/output --work /app/work

# Step 1–2：Agent 逐材料抽取证据、写 manuscript.md 与 evidence_map.json

# Step 3–4：收口复核（退出码 0 表示通过）
python3 scripts/main.py verify --input /input --output /app/output --work /app/work

# 提交/交付前自检（不产出正式结果）
python3 scripts/selftest.py
```

## 示意输出（标记为示例）

`/app/output/result.json` 示意结构：

```json
{
  "case_id": "example-001",
  "deliverable": "manuscript.md",
  "fact_source_map": "evidence_map.json",
  "version_limits": "version_limits.json",
  "status": "complete",
  "sources": [
    {"ref_id": "S1", "locator": "notes.md", "quote": "n=120, mean 4.2 (SD 0.3)", "role": "数值来源"}
  ],
  "uncertainty": [],
  "pending_human": [],
  "change_log": [{"ts": "2026-01-01T00:00:00Z", "step": "skeleton", "note": "示例"}]
}
```

`/app/output/manuscript.md` 示意（节选）：

```markdown
## 3 结果
共纳入 120 例样本（n=120），均值为 4.2（SD 0.3）[S:S1]。……
```

# 最小调用示例（示例，非正式案例）

以下为**示意**：正式测试案例由平台提供，本示例仅演示接口与完整流程，
输出内容为示例标记，不冒充正式案例结果。

## 输入

`/input/case.json`：

```json
{
  "id": "case-example",
  "output_contract": {
    "deliverable": null,
    "fact_source_map": [],
    "version_boundary": []
  }
}
```

`/input/task.md`：

```text
# 任务
润色下列英文摘要，保持事实与强度，消除中式英语，术语统一。
```

`/input/draft.md`：

```text
The proposed method reaches 98.3% accuracy on 5 datasets (Fig. 1),
indicating that it is effective.
```

## 调用

```bash
cd /skills/weekly-skill
python3 -B scripts/main.py scaffold   # 0–15s：落盘 partial 骨架
# 模型填 edits.json（阶段 1–2）
python3 -B scripts/main.py verify     # 阶段 3–4：确定性校验
python3 -B scripts/main.py finalize   # 阶段 5：组装产物
python3 -B scripts/main.py selfcheck  # 阶段 6：自检
```

## 示意输出（标为示例）

`/app/output/manuscript.md` 含：

```text
# 润色后文本（可编辑正稿）
The proposed method achieves 98.3% accuracy on five datasets (Fig. 1),
indicating that it is effective.

## 逐句修改说明
| # | 原文 | 改文 | 类型 | 理由 |
|---|---|---|---|---|
| 1 | reaches 98.3% accuracy | achieves 98.3% accuracy | register | 动词语域更贴合期刊，未改数值 |
```

`/app/output/result.json` 含 `case_id`、`status`、`deliverable`、
`fact_source_map`（每条带 `locator` + 逐字 `quote`）、`sources`、
`uncertainty`、`pending_human`、`change_log`。

> 本示例中的文本与数值均为示意，不代表任何真实科研案例或评测结果。

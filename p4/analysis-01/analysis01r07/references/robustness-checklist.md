# 健壮性自检参考（robustness-checklist）

本文件是 SKILL.md 第 7 节的操作细则，供 Agent 与评审复核“异常场景优雅降级”这一侧重。**所有场景的目标相同：退出码 0、留下诚实可复核的合法 result.json、不编造任何数据。**

## 1. 故障注入矩阵

| # | 场景 | 构造方式 | 期望 status | 期望证据 |
|---|---|---|---|---|
| R1 | 空输入目录 | `/input` 无任何文件 | `insufficient_evidence` | `pending_human` 列出缺口；result.json 合法 |
| R2 | 坏 JSON | case.json 写入非法 JSON | 至少 `partial` | `errors` 记录解析失败；case_id 用文件名兜底 |
| R3 | 坏编码 / BOM | 表为 GBK/GB18030 且带 BOM | 正常或 `partial` | 编码级联解码成功，`degradations` 记录替换字符 |
| R4 | 二进制内容 | 文件含 NUL 字节 | `input_error` 或 `partial` | `degradations`/`errors` 标记 binary |
| R5 | 缺参数 | 不传 `--input/--output/--work` | 默认路径 `/input`、`/app/output`、`/app/work` | 进程不崩溃、退出码 0 |
| R6 | 未知参数 | 传入多余参数 | 忽略并继续 | 进程不崩溃 |
| R7 | 超大文件 | 单文件 > 2MB | `partial` | `degradations` 标 `truncated`；只预览前 N 行，不整表进内存 |
| R8 | 写盘失败 | 输出目录只读 | 至少骨架 result.json | `degradations` 记录失败 artifact；不崩溃 |
| R9 | 单位不明 | 规则缺失换算因子 | `partial` | `pending_human` 标记，不猜换算 |
| R10 | 主键冲突 | 多列候选主键或无主键 | `partial` | `uncertainty`/`pending_human` 标记，不静默删行 |
| R11 | 空表/仅表头 | 表无数据行 | `input_error` | `errors` 记录；不生成空结论 |

## 2. 验收标准

1. **退出码**：`scripts/main.py` 与 `scripts/selftest.py` 在以上场景均退出 0（selftest 只在断言失败时返回 1，用于本地诊断，不影响正式运行）。
2. **结果合法**：`result.json` 可被 `json.loads` 解析，含 `case_id`、`status`、`sources`、`uncertainty`、`change_log`。
3. **契约完整**：case.json 的 `output_contract` 每个键都出现在 result.json（缺值 `null`/`[]`），未知键不删除。
4. **确定性**：同一输入重复运行两次，除 `runtime` 时间戳外，数据结果与字段数值一致。
5. **可复核**：每个关键结论带 `locator` + 逐字 `quote`；报告数字与 `cleaned_data.csv` 一致。

## 3. 本地自检命令

```bash
cd /skills/weekly-skill
python scripts/selftest.py
python scripts/main.py --phase selfcheck --input /input --output /app/output --work /app/work
```

`selftest.py` 在临时目录内构造 R1/R2/R5 + 一个合成案例，不读写用户文件；`--phase selfcheck` 校验真实产物。二者都通过后再结束本次任务。

## 4. 何时判定为“可交付”

- `selftest.py` 4/4 通过；
- `--phase selfcheck` 的 `selfcheck.json` `overall == "pass"`；
- 若 `overall == "fail"`，逐条补齐缺口并重跑，而不是把 fail 藏起来。

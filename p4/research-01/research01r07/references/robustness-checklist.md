# 异常场景矩阵（鲁棒性变体）

本表是 `SKILL.md` 第 7 节"降级策略"的执行级说明。每行是一个必须在交付前实测的场景，脚本 `scripts/selftest.py` 覆盖其中大部分；其余由 Agent 按说明处置。

## 场景与预期行为

| # | 场景 | 预期行为 | 落在哪个字段 |
|---|---|---|---|
| 1 | 输入目录不存在 | `discover` 返回 `exists:false`，不抛异常 | `errors` |
| 2 | 输入目录为空 | `skeleton` 写 `status=insufficient_evidence` | `status`、`pending_human` |
| 3 | case.json 是坏 JSON | `skeleton` 写 `status=input_error`，契约回退默认字段 | `status`、`limitations` |
| 4 | 文本编码不是 UTF-8 | `read_text` 依次尝试 utf-8 → gb18030 → latin-1 | `degradations.encoding` |
| 5 | 文本含 BOM / NUL 字节 | 剥离 BOM 与 NUL，再解码 | `degradations.bom` |
| 6 | 单文件超过 2MB | 截断保留前 2MB，不整文件丢弃 | `degradations.truncated` |
| 7 | 缺 `--input/--output/--work` 参数 | 使用缺省 `/input`、`/app/output`、`/app/work` | 无（正常缺省） |
| 8 | 写盘目标不可写 | `write_json` 返回错误记录，不崩；顶层再兜底写最小合法产物 | `errors` |
| 9 | payload 不是 JSON 对象 | `merge` 返回 `ok:false`，不改写现有 result.json | 返回 JSON |
| 10 | PDF 无文本层 | 证据行 `judgment=extraction_failed`，建议 OCR | `evidence_matrix[].judgment` |
| 11 | 证据互相冲突 | 两来源并列写入 `uncertainty.conflicts`，不静默择一 | `uncertainty.conflicts` |
| 12 | 重复运行 | 证据 id、排序、merge 幂等，结构一致（时间戳是真实运行记录） | 整体布局 |
| 13 | 缺软件/缺权限 | 记录到 `limitations`/`errors`，不伪造软件状态 | `limitations` |

## 判定原则

1. **永远先落盘**：P0 阶段（前 3 分钟）必须写出一版合法 `result.json`，哪怕只有 `status=partial` 的空骨架；后续只增量改进，禁止清空重建。
2. **读失败不致命**：单个文件读失败只影响该文件，其余材料继续处理；`errors` 记录的是"哪些东西处理不了"，不是"整个任务失败"。
3. **写失败可降级**：所有写盘走原子替换（临时文件 + `os.replace`），失败时保留旧文件或最小合法产物，不产生半截 JSON。
4. **退出码约定**：数据/输入/写盘类问题一律退出码 0（Harness 仍能拿到合法产物）；只有脚本本身的未预期异常才非零退出，用于暴露真实 bug。
5. **不编造**：材料没有的结论写 `pending_human` 或 `gaps`，不生成示例答案冒充正式结果；`status` 如实反映完成度。

## 交付前自查命令

```bash
cd <skill根目录>
python3 -B scripts/selftest.py          # 10 个场景，应全 PASS、退出码 0
python3 -B scripts/main.py --help        # 退出码 0
python3 -B scripts/main.py selfcheck --output <测试输出目录> --work <测试工作目录>
```

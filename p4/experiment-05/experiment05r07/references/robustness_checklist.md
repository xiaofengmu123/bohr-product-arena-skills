# 鲁棒性检查清单（变体 #鲁棒性）

本版与基线包的设计取舍：**把“绝不崩溃、确定性、可恢复”放在首位**。所有脚本遵循同一降级阶梯，任何输入都尽量以退出码 0 收尾并留下合法产物。执行 `/app/output/` 任务时逐条核对。

## 降级阶梯

1. **发现**：确定性扫描 `/input`（目录、文件名均排序，跳过符号链接）。
2. **读取**：编码阶梯 `utf-8-sig → utf-8 → gbk → latin-1`（UTF-16 仅按 BOM 识别）；含 NUL 视为二进制；超 2MB 跳过。
3. **记录**：坏 JSON 进 `errors`，坏编码/超限/缺工具进 `degradations`，其余继续处理。
4. **降级**：材料不足 → `insufficient_evidence`；对照/重复不足 → 降置信并进 `limitations`/`pending_human`。
5. **继续**：任何单文件失败不阻断其余文件；任何阶段先落盘 partial 骨架。

## 场景 → 要求

| 场景 | 期望行为 | 验证方式 |
|---|---|---|
| 空输入目录 | 退出码 0，`status=insufficient_evidence`，列出缺口，不抛异常 | `selftest.py` 场景 1 |
| 坏 JSON | 记入 `errors`，继续处理其余材料 | `selftest.py` 场景 2 |
| 坏编码 | 记入 `degradations`，不崩溃 | `selftest.py` 场景 3 |
| 超大文件（>2MB） | 跳过并记入 `degradations` | `selftest.py` 场景 4 |
| 缺参数/未知键 | `--input/--output/--work` 缺省 `/input`、`/app/output`、`/app/work`；未知键不改名不删除 | `selftest.py` 场景 5 |
| 二进制文件（NUL 字节） | 标记 `binary`，不作文本解析 | `read_text` 返回 note |
| 单位不一致/超量程 | 保留原始单位，写 `uncertainty`，不做无依据换算 | 画像 `parse_notes` |
| 写盘失败 | 捕获异常，降级最小合法产物，记入 `errors` | `write_*_atomic` try/except |
| 重复运行 | 同一输入结果一致（无随机数依赖、排序确定） | `selftest.py` 场景 7 |

## 脚本不变量

- 仅 Python 标准库；运行目录下 `scripts/` 内脚本可独立以 `python scripts/<name>.py` 调用。
- 脚本之间通过 `import main as core` 复用同一套鲁棒读取与写盘，避免多份实现漂移。
- 所有 JSON 用 `sort_keys=True` 落盘，保证输出字节级确定性。
- 任何脚本异常都被捕获并以退出码 0/1 + stderr 说明返回，绝不静默吞掉。
- 脚本只做确定性计算与骨架/校验/渲染，**不生成科学结论**；结论由 Agent 依据材料填写并附 evidence。

## 定稿前自检命令

```bash
python scripts/selftest.py
python scripts/main.py validate --input /app/output/result.json
```

`selftest.py` 全绿 + `validate` 0 problem 才可定稿。

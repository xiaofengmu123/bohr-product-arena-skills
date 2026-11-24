# 健壮性与降级矩阵

本 Skill 为**鲁棒性变体**：脚本全程不抛未捕获异常，任何失败都写成 `errors`/`degradations`/`exceptions`，退出码 0，并始终尽力产出合法 `result.json`。

## 1. 降级矩阵

| 场景 | 处置 | result.json 表现 |
|---|---|---|
| `/input` 缺失 | 不崩溃 | `status=input_error`，`case_id=unknown-case`，`errors` 记原因 |
| `case.json` 坏 JSON / 坏编码 / 空文件 | 逐编码回退（utf-8-sig→utf-8→gb18030→latin-1），仍失败则降级 | `status=input_error`，`errors` 记原因 |
| `output_contract` 缺失 | 回退七字段 | 字段仍齐全 |
| 输入目录空（无材料） | 正常结束 | `status=insufficient_evidence`，`uncertainty`/`pending_human` 列缺口 |
| 单文件 >2MB（默认上限，`--file-limit` 可调） | 跳过该文件，不读入内存 | `degradations` 记录 |
| draft.json 缺失/不完整 | 正常结束，不编造内容 | `status=partial`，`pending_human` 提示补 draft |
| 无 python-pptx / LibreOffice | 用纯标准库构建最小 pptx 或 SVG 预览 | `exceptions` 标注降级 |
| PDF 页数与 pptx 页数不一致 | 检测并记录 | `degradations` 记录 |
| 写盘失败 | 降级到 cwd 下的 `skill_work`/`skill_output` | `degradations` 记录，进程不崩溃 |

## 2. 运行稳定性原则
1. **确定性**：同一输入两次运行产物一致；不引入随机数、不依赖系统时区之外的隐式状态。
2. **先落骨架**：`main.py` 在读到 `case.json` 后立即写 `status=partial` 骨架，之后每填一部分重写，保证任何中途异常都有可交接产物。
3. **单次工具**：脚本只做发现/校验/构建/导出，不串成多步流水线；读材料、抽主张、写 draft 的主体工作由 Agent 完成。
4. **缺省路径**：`--input/--work/--output` 缺省 `/input`、`/app/work`、`/app/output`；未知参数由 argparse 明确报错退出（非崩溃）。

## 3. 自检
```bash
python3 -B scripts/main.py --help          # 退出码 0，打印用法
python3 -B scripts/selftest.py             # 空输入/坏 JSON/缺参数三种场景各跑一次，全 PASS
python3 -B scripts/main.py --input /input --work /app/work --output /app/output
```
产物要求：`result.json` 存在且为合法 JSON、`case_id` 非空、`status` 在允许集合内；主产物文件存在，PDF 以 `%PDF-` 开头，pptx 可用 zipfile 打开。

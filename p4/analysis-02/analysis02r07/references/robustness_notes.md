# 鲁棒性与降级规则（robustness_notes）

本版以**鲁棒性**为首要设计目标。原则：**任何输入/环境异常都产出合法产物并退出码 0，
绝不崩溃、绝不编造、绝不静默篡改数据**。

## 1. 输入发现（先发现，再假设）

- 扫描输入目录，不假设固定文件名/键名：`case.json`、`materials/`、`notes.md` 等都要能识别。
- 单文件扫描上限 64MB，超限跳过并记 `degradations`；文本/CSV/JSON 读取上限 10MB。
- 表格行数封顶 200_000、列数封顶 500，防止超大文件 OOM。
- 空行（常见尾部换行伪行）在 CSV 解析时剔除，避免被误计为缺失行。

## 2. 编码与坏 JSON 回退

文本读取按 `utf-8-sig → utf-8 → latin-1 → cp1252` 顺序回退，记录实际编码。
JSON 解析失败时尝试按错误位置抢救合法前缀；仍失败则记 `errors`，继续处理其余材料。

## 3. 缺参数 / 未知参数

`--input/--output/--work` 缺省 `/input`、`/app/output`、`/app/work`；未知参数被忽略。
命令行解析用 `parse_known_args`，不因 harness 附加参数而报错。

## 4. 降级矩阵（诚实优先）

| 场景 | 处置 | status |
|---|---|---|
| 空输入目录 | 列缺口，写合法 result.json | insufficient_evidence |
| 坏 JSON / 坏编码 | 记 errors/degradations，继续其余材料 | partial / input_error |
| 缺参数/未知参数 | 用缺省值/忽略 | 正常流程 |
| 超大文件 | 跳过 + degradation | partial |
| 组内方差为 0 / n<2 / 缺失过高 | 该检验标 `not_applicable`，不给 p | partial/ok |
| 字段单位不明 | 标 `unit_unknown`，不猜单位 | ok + uncertainty |
| scipy/numpy 不可用 | statlib.py 内置纯标准库近似 | ok + 口径说明 |
| 写盘失败 | 降级最小合法 result.json | partial |

## 5. 契约自适应回填

- `output_contract` 每个键逐键回填：缺值 `null`/`[]`，不改名、不删未知键。
- 语义别名映射（见 core_impl.py `CONTRACT_ALIASES`）把实际结果填入
  `effect_size`、`confidence_interval` 等异名字段；未知键保留缺省值。
- 信封字段 `case_id/status/sources/uncertainty/change_log/errors/degradations/pending_human/runtime`
  始终存在。

## 6. 确定性复现

- 无随机数、无隐式全局状态；JSON 输出 `sort_keys=True`（纯 ASCII，`ensure_ascii=True`）。
- 同输入两次运行结果逐字节一致；校验：`python scripts/main.py selftest` 与 `selfcheck` 退出码 0。

## 7. 证据溯源

- 每个关键结论给 `locator`（文件#行/列）+ 逐字 `quote` 或计算依据。
- 计算依据链：`cleaned/*.csv`（清洗后数据）+ `code/`（实际运行脚本快照）+
  `quality_checks.json`（清洗规则）+ `analysis_report.md`（结果汇总）。

## 8. 顶层兜底

`core_impl.run` 顶层 `catch-all` 保证：任何命令抛异常时，仍写出合法 partial result.json、
记录 `errors`/`degradations`、进程退出码 0。宁可 partial，不零产物。

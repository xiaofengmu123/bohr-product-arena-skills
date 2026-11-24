# 确定性设计说明与降级矩阵

本文件解释本 Skill 的确定性约定（同输入两次运行产物一致）与异常处置矩阵，供作者、复核者与后续 Agent 复用。

## 1. 为什么做确定性

评测以"公平对照"为前提：相同材料、相同模型、相同预算下，Skill 输出的可复核性依赖**可重跑**。若产物混入时间戳、随机排序、hostname/PID，两次运行即使内容等价也无法机械比对，复核成本上升，评分中的"增益可验证性""运行稳定性"受损。

## 2. 确定性约定（逐条）

1. **规范 JSON**：`content_sha256` 对 `json.dumps(sort_keys=True, ensure_ascii=False, separators=(",", ":"))` 的结果取 SHA-256，与字段书写顺序无关。
2. **固定落盘键序**：`result.json` 按 `case_id → output_contract 字段（原序）→ status → artifacts → sources → uncertainty → pending_human → errors → degradations → limitations → change_log → runtime → determinism` 的顺序写，插值顺序固定即字节固定。
3. **无时间戳进内容**：`change_log` 只用步骤标签 `skeleton / inventory / draft / improve / finalize`；不写 `datetime.now()`、hostname、PID、绝对临时路径到内容字段。
4. **材料扫描确定性**：`os.walk` 的目录与文件均排序，候选事实按 `(locator, kind, quote)` 排序，跨平台一致。
5. **唯一例外 `runtime`**：墙钟秒数是环境相关的诚实记录，**排除在 `content_sha256` 之外**，并在 `determinism.excluded_fields` 中声明。除它之外任何字段变化都会改变哈希，可被 `validate` 检出。

## 3. 验证方式

```bash
python3 scripts/selftest.py          # 合成输入跑两遍，断言哈希一致；含空目录/坏 JSON 降级
python3 scripts/main.py validate      # 校验契约、证据、产物路径与哈希
```

`selftest.py` 用完全确定的合成案例（config.yaml / train.log / notes.txt），不依赖网络与外部工具；退出码 0 表示通过，1 表示失败。**任何人对同一包跑 `selftest.py` 应得到相同 PASS 行**。

## 4. 降级矩阵（诚实优先）

| 场景 | 检测方式 | 处置 | status |
|-|-|-|-|
| `/input` 无材料 | `skeleton` 扫描 | 写 `limitations` + `pending_human`，不生成示例答案 | `insufficient_evidence` |
| `case.json` 缺失/坏 JSON | `load_json_safe` 失败 | 写 `errors`，`case_id=unknown` | `input_error` |
| 单个材料坏编码 | `read_text_safe` 降级 latin-1 | 记 `degradations`，继续其余材料 | 维持 |
| 单文件 > 2MB | `_scan_materials` | 跳过并记 `degradations` | 维持 |
| 缺 pdftotext/Office | 运行失败捕获 | 记 `degradations`，改用题面 pdf-text-tool/office-renderer/ocr-tool | 维持 |
| 参数无来源/冲突 | 证据核对 | 标 `pending_human` 或 `source_conflict`，不自行裁定 | 维持 |
| 时间/请求耗尽 | 预算监控 | 先保 `result.json` + 初稿 + 证据映射，再增量 | `partial` |

原则：**宁交诚实的 `partial`，不交编造的 `ok`**。任何无法从材料核实的数值都不得以"合理推测"补入正文。

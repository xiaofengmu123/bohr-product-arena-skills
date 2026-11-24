# 自检与确定性核验手册

本文件是交付前与运行期自检的通过标准。SKILL.md 第 5 节的清单逐条对应本文件的检查方法与判定。

## 1. 运行前自检（交付前，作者执行）

```bash
cd /skills/weekly-skill          # 或本包根目录
python -B scripts/selftest.py    # 退出码 0 为通过
python -B scripts/main.py --help # 退出码 0
python -B scripts/main.py skeleton --output ./demo/out   # 空/坏输入也能落盘
```

`selftest.py` 依次检查：
1. SKILL.md 存在且 frontmatter 含 name/description/version。
2. `main.py` 与 `selftest.py` 的 `--help` 退出码为 0。
3. 确定性：固定演示输入下 `evidence` 连续跑两次，`evidence.json` SHA-256 逐字节一致。
4. 证据抽取内容非空（候选论点、数值、越界句均产出）。

## 2. 运行期自检（每案例，模型执行）

步骤 4 执行 `python scripts/main.py verify`，检查项与通过标准：

| 检查项 | 通过标准 |
|-|-|
| valid_json | result.json 可解析 |
| required_fields | 含 case_id/status/sources/uncertainty/pending_human/change_log |
| status_value | status ∈ {complete, partial, failed, insufficient_evidence, input_error} |
| sources_provenance | 每条 source 含 claim_id 与逐字 quote |
| sources_locator | 每条 source 含 locator |
| change_log | 至少一条记录 |
| main_product | diagnosis 或 edit_plan 非空（in_progress/partial 状态除外） |

WARN 需人工确认后修复；FAIL 必须修复后才能 finalize 为 complete。

## 3. 确定性约定

- 同输入两次运行，`evidence.json`、`sources`、`diagnosis`、`edit_plan` 必须一致。
- 允许不同的字段：`change_log[].time`、`runtime` 内时间戳。
- 模型不得依赖随机采样或"凭记忆补数"；所有数值与引文必须来自脚本候选或逐字读取原文。
- 交付前 `selftest.py` 的 determinism 项必须 PASS。

## 4. 异常降级映射

| 场景 | status | 处置 |
|-|-|-|
| 空输入目录 / 无材料 | insufficient_evidence | 落盘合法结果，sources/uncertainty 说明缺口 |
| 坏 JSON / 坏编码 | input_error（可继续处理其余材料） | 记入 errors/degradations |
| 无 pdftotext / 无 OCR / 无渲染器 | partial | uncertainty/limitations 标注"未提供/不可用"，字段留空 |
| 图/公式读数不清、单位不明 | partial | pending_human + "[待确认：xxx]" 占位 |
| 稿件截断 / 无参考文献 / 章节缺失 | partial | limitations 说明影响范围，缺失部分不臆测 |
| 运行失败或超时 | failed（保留已落盘 partial） | 说明失败原因，change_log 记录 |

任何降级都不允许伪造读数、软件状态或科学数据；`status` 必须如实反映可用证据范围。

## 5. 与评分维度对应

- 实际效果：第 2 节 valid_json / required_fields / sources_provenance / main_product。
- 使用增益：第 1 节确定性 + SKILL.md 第 4 节骨架先行、增量改进。
- 创新增益：脚本候选锚点（论点/数值/越界/近重复）+ references/structure-taxonomy.md 统一规则。
- 可靠体验：`--help` 可运行、确定性双跑一致、异常降级映射齐全。
- 生态贡献：本包仅依赖 Python 标准库，规则、清单与脚本可复用。

# 确定性与降级规则

本包的设计取舍是**确定性优先**：同一输入两次运行产出字节一致，且任何缺口都
显式记录，绝不编造。运行结果与真实执行一致，不写任何评测分数或伪造运行记录。

## 1. 确定性规则（scripts/ 强制执行）

1. **输入指纹**：`read_input.py` 对 `/input` 下所有文件按相对路径排序后计算
   `sha256` 拼接摘要，写入 `run_fingerprint`；输入变了指纹必变。
2. **排序序列化**：所有脚本输出 JSON 均 `sort_keys=True`、`ensure_ascii=False`、
   `indent=2`、末尾换行；目录遍历与文件列表一律排序。
3. **无墙钟时间入正文字段**：`change_log` 用单调 `step` + `fingerprint` 记录
   动作，不写 wall-clock 时间戳，因此两次运行结果可比对。若需要记录执行时长，
   只写入运行时元数据字段，不进入决定内容的关键字段。
4. **确定性自检**：`python scripts/selftest.py determinism --summary …` 对同一
   summary 连续两次 `build_skeleton`，断言字节一致；不一致即视为缺陷。
5. **同输入同产物**：骨架、要素映射、成稿、自检各步骤的中间产物均来自可重放的
   脚本调用，Agent 不得仅凭会话“记住”状态；每次改动立即写回文件。

## 2. 降级策略（禁止编造）

| 场景 | 处置 |
|---|---|
| 材料缺失 | `uncertainty`/`version_boundaries` 标“缺失/无法判断”，字段留 `null` 或空；不补造数值、文献、作者、政策口径 |
| 引用无法核实 | 保留原文位置并标 `unverified`，不伪造 DOI/URL |
| PDF 无本地 pdftotext | 如实写 `limitations`，改用平台 pdf-text-tool 结果，不假装已解析 |
| Office/幻灯片无法渲染 | 交付纯文本/结构化产物，写 `limitations`，不假装已渲染 |
| 输入超长/跨度过大 | 截取关键段落并在 `uncertainty` 说明“已截断/未覆盖” |
| 运行失败 | `status=failed`，`change_log` 记录失败原因与已完成中间产物，result.json 仍须落盘 |
| 图表读不清（单位/误差棒/统计量缺失） | 只写描述性观察，标 `uncertainty`，不猜数值 |

## 3. 禁词（selftest.py 会扫描）

首次、国际领先、填补空白、颠覆性、革命性、世界首创、全球领先、国内首创、
全球第一、重大突破，以及英文对应词（groundbreaking、revolutionary、
world-first、unprecedented、first-in-class 等）。命中即提示：要么删除，要么
确认是直接引用的原文片段并附 `quote`/`locator`。

## 4. 提交前最小自检（在目标环境跑一次）

```bash
python3 -B scripts/read_input.py --input-dir /input --work /app/work
python3 -B scripts/build_result.py --init --summary /app/work/input_summary.json \
  --result /app/output/result.json
python3 -B scripts/selftest.py check --result /app/output/result.json \
  --summary /app/work/input_summary.json --output-dir /app/output
python3 -B scripts/selftest.py determinism --summary /app/work/input_summary.json
```

四步退出码均为 0（check 可为 2 表示仅警告），即可判定结构合法、可回溯、可重复。

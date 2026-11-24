# 鲁棒性与降级矩阵（variant：robustness）

本 Skill 的变体侧重点是**鲁棒性**：输入异常时优雅降级、不崩溃、不编造，同时保持输出契约完整。
脚本只依赖 Python 标准库，确定性布局（固定随机种子、固定画布、排序后的元素顺序），保证同输入复现同结构。

## 1. 降级矩阵

| 场景 | 识别方式 | 处置 | 退出码 |
|-|-|-|-|
| 空输入目录 | 扫描无 case.json/task.md/材料文件 | 写 `status=failed` 合法骨架，limitations 说明“输入为空”，pending_human 列出所需材料 | 0 |
| 坏 JSON | `json.loads` 失败 | 记录到 `errors`/`degradations`，尝试顶层花括号截断修复；失败则从 task.md 推导 case_id 继续，其余材料照常处理 | 0 |
| 坏编码 / BOM | `utf-8-sig`→`utf-8`→`gb18030`→`latin-1` 逐级回退 | 记录解码失败，用替换字符读取，不中断 | 0 |
| 缺参数 / 未知参数 | argparse | `--input/--output/--work` 缺省 `/input`、`/app/output`、`/app/work`；未知参数由 argparse 报错退出 | 0 |
| 超大文件 | 单文件 >2MB（`MAX_TEXT_FILE_BYTES`） | 跳过并记入 `degradations`，其余文件继续 | 0 |
| 缺关键数值 / 实体歧义 | 抽取结果为 null / 方向不明 | 字段置 `null`，uncertainty 标注；方向歧义拆 id 或用无向连接 | 0 |
| 字体缺失 / 导出失败 | 渲染器探测失败 | 保留 SVG，`exports[].status=preview_not_rendered`，limitations 标注；不伪造 PNG | 0 |
| 写盘失败 | `os.replace`/写文件异常 | 降级到最小合法产物（failed 骨架），不让进程崩溃 | 0 |
| 无 case.json | 扫描不到 | 写 failed 骨架、记 limitation，正常退出并上报阻塞，不继续依赖步骤 | 0 |

## 2. 输入发现顺序（先发现，再假设）

1. 递归扫描输入目录，不假定固定文件名/键名：`case.json`、`task.md`、`materials/`、CSV、图片均需识别。
2. 文本按 `utf-8-sig` 读取并容错；单文件 >2MB 跳过。
3. `output_contract` 若存在则**原样回填全部键**；缺值给 `null`/`[]`，不改名、不删未知键。

## 3. 确定性规则

- `stable_id()` 用 SHA1（前 12 位），元素 id 稳定。
- SVG 布局固定：画布默认 1600×1100，`layer-title` + 四个面板（question/method/results/conclusion）+ `layer-legend`；面板 4 列、从左到右对应“问题→方法→结果→结论”阅读顺序。
- 颜色用 Okabe-Ito 色盲安全色板；`constraints.palette` 为 grayscale 时切换灰度序列。
- 图表数据优先取 CSV/TSV 数值列（`extract_tabular`），否则取文本数值（`extract_data`）；柱长编码数值，柱标签带数值与单位。

## 4. 可复核性要求

- `figure_spec.json`、`case_normalized.json`、`fig_manifest.json`、`validate_report.json` 落盘到 `/app/work/`，供独立复核。
- SVG 每个图元带 `data-source`/`data-entity`/`data-relation`/`data-value` 属性；`<text>` 保持可编辑。
- `change_log` 每次改写递增；`runtime` 记录 skill 版本与起止时间。

## 5. 诚实边界

- 没有真实材料时输出“材料不足”，不填充示例。
- 不伪造引用、DOI、数值、渲染结果；缺失写 `not_reported` 并进 `pending_human`。
- 科学判断（四要素措辞、实体命名、箭头方向）由 Agent 在 `figure_spec.json` 中确认，脚本只做确定性脚手架；这是“native/LLM Judge”而非字面导出通过。

# 契约与降级（contract）

## 1. result.json 顶层结构
`output_contract` 声明的字段原样回填；本题缺省契约七字段：

| 字段 | 要求 |
|---|---|
| `case_id` | 字符串，取自 case.json 原样回填 |
| `status` | ok / partial / insufficient_evidence / input_error |
| `editable_source` | {path,format,slides,mode,template:{path,master,layouts_used},sha256} |
| `export_artifact` | {pdf:{path,pages,engine}}；PDF 页数 == slides |
| `content_map` | 逐页 n/title/claim/role/evidence[]/assets/notes |
| `compliance_checks` | 页数、模板继承指纹、每页 role/claim/evidence、edit 局部性 |
| `exceptions` | 图表无法提取、公式为图片、模板占位不足等 |

保留信封字段：`sources`、`uncertainty`、`pending_human`、`change_log`、`errors`、`degradations`、`runtime`。

## 2. 页级 content_map 与证据
```json
{"n":1,"title":"…","claim":"一句话主张","role":"method",
 "evidence":[{"type":"section|figure|table|page","id":"S1","locator":"para.3",
              "quote":"逐字原文","quote_verified":true}],
 "assets":[],"notes":"讲稿备注"}
```
硬要求：每个关键结论/数值/图元指回材料逐字片段（locator+quote），`quote_verified` 由脚本对提取正文做子串命中计算，不命中即 WARN，不伪造引文。

## 3. mode 判定
- `create`：无输入 pptx，从零生成（内置母版+版式）。
- `edit`：有输入 pptx，只改目标页文本（edits 列表），其余页与母版/版式不变。
- `adapt`：有输入 pptx，真继承模板母版/版式/主题，追加新页并指向模板既有版式。

## 4. 降级决策表
| 情形 | 处置 | 结果标注 |
|-|-|-|
| 论文 PDF 缺失/无文本层 | 只用可确证材料，其余页标 limitation | insufficient_evidence/partial |
| 图缺失/位图无数据 | 引用原图+定位，不重绘不补值 | uncertainty |
| 模板槽位不足 | 映射最近似版式 | uncertainty |
| 字体缺失 | font-discovery 选替代字体 | uncertainty |
| 页面溢出 | 删要点/拆页，字号≥16pt | change_log |
| 导出失败 | 交付源文件+部分预览 | partial + 失败原因 |
| 缺 python-pptx/soffice | 回退 Markdown/HTML | partial，不伪造渲染 |
| 数字/事实不确定 | 不进页面主张 | uncertainty/pending_human |
| 坏 JSON/坏编码/空输入 | 写合法 result.json，退出码 0 | input_error/insufficient_evidence |

## 5. 可核验性
- 脚本不联网、无随机性；固定 ZIP 时间戳保证同输入同输出。
- 所有输出路径相对 `/app/output`；产物 sha256 写入 editable_source。
- 不虚构引用、DOI、数值、运行日志或分数；缺失写 `not_reported`/`pending_human`。

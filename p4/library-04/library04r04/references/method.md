# 方法说明：证据溯源、缺证判据与降级设计

本文件解释本 Skill 的方法取舍，重点回答“为什么敢说没有、如何让他人复跑”，是 `checklist.md` 的方法论补充。

## 1. 核心取向：把三类信息分开

普通问答容易把“结论、证据、缺口”混在一起。本 Skill 强制三者分离：

- **结论**（`answers[].answer`）：只写有依据或明确“无依据”的陈述。
- **证据**（`answers[].evidence[]`）：`file`+`sha256`+`locator`+逐字 `quote`，独立可核。
- **缺口**（`limitations[]`/`uncertainty[]`/`pending_human[]`）：明确“还差什么、影响什么”。

一个正确答案是“断言—证据对”的最小组合；证据不能回链的断言一律不写 `certain`。

## 2. 缺证判据（不编造的关键）

对每个问题，Agent 必须执行以下判定，任一命中即落 `insufficient_evidence`，答案正文写“未在知识库中找到依据”，并说明缺什么：

1. 检索后没有任何文件片段能支持该问题（召回为空）。
2. 命中片段与问题不构成逻辑支撑（语义不相关，禁止强行引用）。
3. 唯一命中片段来自不可读文件（`unreadable`）且无 OCR 文本。
4. 两个来源对同一指标矛盾（落 `conflict`，同时保留双方）。

判断“有依据”的最低标准：至少一条证据满足 `quote` 逐字命中、`locator` 可打开、且语义上支撑答案主体。

## 3. 六维度组织如何支撑证据与版本

- **哈希去重**：SHA256 相同即重复，避免“多副本被当成多论据”的错误加权；引用时选择一条代表性路径并记录副本族。
- **版本分组**：`version_base` 归并 v1/v2/final/draft/date 标记，`version_families` 保留每版的哈希差异；引用必须落在**具体版本**的段落，不能用“新版本”替代“旧版本”的原文。
- **权限与来源**：`permissions` 记录 mode/可读位/uid/gid，`source` 记录来源标签；无读权限的文件只做元数据，不给出内容结论。
- **目录层级与编码**：`directory_tree` 提供稳定坐标，`text_encoding` 记录解码方式；`encoding_fallback` 的文件引用前须先人工确认字形无损。

## 4. 分块与召回策略

- 文本：按整文件读入，保留 `total_chars`/`n_lines` 以及前 `snippet` 片段写入 `evidence_index.json` 供检索定位，最终定位用真实行号。
- PDF：优先 `pdftotext -layout`，按换页符分页得到 `#p<n>`；无文本层标 `needs_ocr`，不编造页码。
- Office/镜像/压缩：不强制解析，`type` 如实标注；仅当外部工具可用时才补充内容，缺失时记 `limitations`。
- 检索顺序建议：`evidence_index.json`（快速判有无）→ 原文件定位（行号/页码）→ 逐字摘录（quote）。三步分离可避免“先有答案再找引用”的反向偏误。

## 5. 降级与恢复矩阵

| 触发条件 | 识别方式 | 落盘字段 | 产物状态 |
|---|---|---|---|
| 输入目录为空 | 扫描无文件 | `limitations[empty_input_directory]` | `insufficient_evidence` |
| `case.json` 缺失 | 加载失败 | `limitations[case_json_missing]` | `input_error` |
| 坏 JSON/坏编码 | 加载/解码异常 | `errors[]`/`degradations[]` | 其余材料继续处理 |
| 文件不可读/权限不足 | `os.access` 失败 | `limitations[permission_denied]`+`uncertainty[unreadable]` | `partial` |
| PDF 无文本层 | `pdftotext` 输出为空 | `limitations[pdf_needs_ocr]`+`uncertainty[unreadable]` | `partial` |
| 缺 `pdftotext` | `shutil.which` 为 None | `limitations[pdftotext_not_available]` | `partial` |
| 单文件 >2MB | 大小检查 | `limitations[file_over_2MB_skipped]` | 哈希/元数据保留 |
| 写盘失败 | 捕获并最小化写 | 最小合法 JSON | 不崩溃 |
| 超时/超请求 | 阶段预算检查 | `failures_recovery[]`+`status=partial` | 先落盘部分结果 |

原则：**所有降级都显式化、可定位、可恢复**；不存在“静默失败”。恢复路径给出下一步（重跑某阶段、安装 `poppler-utils`、人工 OCR、人工复核编码）。

## 6. 可复现性约定

- 所有文件列表、目录、哈希组、版本族、契约键均按字典序或固定顺序排序，并对时间戳只记录“真实发生时间”，不使用随机数。
- 两次运行同一输入（同一时刻截取的 mtime 除外）应产出相同的 `sources`/`library`/`version_families`。
- 记录 `runtime` 仅作诊断，不参与任何评分断言；禁止编造基准分、榜单分或对照白板结果。
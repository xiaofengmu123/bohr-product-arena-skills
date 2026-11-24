# 六维度覆盖、证据定位与复核/自检清单

本文件是 SKILL.md 第 6 节自检清单的执行细则，供 Agent 与复核人按同一标准落地，避免“回答通顺但引用对不上”的典型失败。所有脚本均只依赖 Python 标准库，运行路径以 Skill 根目录为基准，用相对路径调用。

## 1. 六维度覆盖细则（对应“结果完整性”，5 分）

| 维度 | 必须产出 | 数据来源字段 |
|---|---|---|
| 文件类型 | 按扩展名+内容嗅探归类 `text/pdf/office/image/archive/unknown` | `inventory.json → summary.file_types`、`files[].type` |
| 元数据字段 | Markdown frontmatter 的 title/author/date/tags；文件大小、mtime、编码 | `files[].metadata`、`files[].text_encoding` |
| 目录层级 | 相对路径树 + 目录列表（含 mtime） | `directory_tree`、`directories[]` |
| 重复文件 | 按 SHA256 分组，列出同哈希所有路径；空文件单列 | `duplicate_groups[]`、`empty_files[]` |
| 版本差异 | 版本基名分组（v1/v2/final/draft/date），列出成员与哈希 | `version_families[base][]` |
| 权限与来源 | mode 八进制/可读可写位/uid/gid；材料来源标记 | `files[].permissions`、`files[].source` |

复核要求：六项任一缺失，`skel` 视为完整性不达标，回补对应字段后重跑 `inventory.py`。

## 2. 证据定位规范（locator）

- 文本文件：`<相对路径>#L<起>-L<止>`，如 `materials/paper.md#L12-L15`；单行写 `#L12`。
- PDF：优先 `#p<页码>`（由 `pdftotext -layout` 提取后按换页符 `\f` 分页）；无文本层时只写 `file+sha256`，标 `unreadable`/`needs_ocr`。
- 每条引用必须同时给 `file`、`sha256`、`locator`、`quote`；`quote` 为原文逐字片段，禁止改写、禁止“大意转写”。

复核：任取 `answers[].evidence[]`，按 locator 打开原文件，`quote` 必须逐字命中；对不上的记录视为错误，进 `failures_recovery`，不得标 `certain`。

## 3. 不确定性分级细则

| 级别 | 判据 | 必须附带 |
|---|---|---|
| `certain` | 逐字 quote + 单一来源 | `file`/`sha256`/`locator`/`quote` |
| `likely` | 由原文推断或摘要，非逐字 | `reason` |
| `inferred` | 跨多个片段组合推断 | `reason`/`sources` |
| `conflict` | 同一指标两个来源不一致 | 全部来源 + `source_conflict` 标记 |
| `insufficient_evidence` | 库内无依据，需写明缺什么 | `missing` |
| `unreadable` | 文件不可读/扫描件无文本层/权限不足 | `reason`/`missing` |

限制：任何 `uncertainty` 条目都不允许只有级别没有 reason；缺证据的问题答案正文写成“未在知识库中找到依据”，而不是猜一个答案。

## 4. limitations 显式化（本变体的核心差异）

`result.json.limitations[]` 与 `uncertainty[]` 分开：前者记录运行环境/材料自身缺口，每条至少含：

- `limitation`：缺口名称（如 `pdftotext_not_available`、`empty_input_directory`、`permission_denied`、`encoding_fallback`、`file_over_2MB_skipped`、`pdf_needs_ocr`、`output_contract_missing_keys`）。
- `impact`：对结果可靠性的实际影响。
- `files`：涉及的相对路径列表（可为空）。
- `needs_human`：布尔；为真时同步进入 `pending_human`。
- `blocking`：布尔；为真且存在时，`status` 不写 `ok`。

规则：**缺数据/缺软件只记 limitations，不补造数据**。例如缺 `pdftotext` 时写 `pdftotext_not_available` 并把 PDF 标 `unreadable`，禁止凭空生成页码与引文。

## 5. 版本与重复判定启发式

- 重复：**SHA256 相同即重复**，不看文件名；空文件单独列入 `empty_files`，不并入哈希组。
- 版本：文件名含 `v1/v2`、`version-`、`rev-`、`final`、`draft`、`YYYYMMDD` 等标记时归入同一 `version_base`；哈希不同说明内容有差异，`version_families` 保留全部成员供比对。
- 同一 base、同一哈希、不同路径：视为重复副本，不误判为新版本（会同时出现在 `duplicate_groups` 与 `version_families`，属正常）。

## 6. 自检命令（提交/交付前在本机或 Linux 环境跑一遍）

```bash
python3 scripts/main.py --help                      # 期望退出码 0
python3 - <<'PY'
import json, pathlib
r = json.loads(pathlib.Path('/app/output/result.json').read_text(encoding='utf-8'))
assert r.get('case_id') and r.get('status')
assert isinstance(r.get('sources'), list)
assert isinstance(r.get('uncertainty'), list)
assert isinstance(r.get('limitations'), list)
assert isinstance(r.get('change_log'), list)
print('ok', r['status'], r['case_id'])
PY
```

三种降级场景各跑一次并确认不崩溃、退出码 0、产物 JSON 合法：

1. 空输入目录 → `status=insufficient_evidence`，`limitations` 有 `empty_input_directory`。
2. 坏 JSON/坏编码 → `errors`/`degradations` 有记录，其余材料继续处理。
3. 缺 `pdftotext`（或 PATH 无此命令）→ PDF 标 `unreadable`，`limitations` 有 `pdftotext_not_available`。

## 7. 复核人复跑步骤

1. 安装：把包放到 `/skills/weekly-skill`，确认 `python3 scripts/main.py --help` 退出码 0。
2. 运行：`python3 scripts/main.py --input /input --output /app/output --work /app/work`（等价于 skeleton → inventory → finalize）。
3. 核查：`result.json` 含 `case_id`、`status`、`sources`、`uncertainty`、`limitations`、`change_log` 及 output_contract 全部键，JSON 可解析。
4. 回链：任取一条 `answers[].evidence[]`，按 `locator` 逐字核对；对不上即错误。
5. 复查：按第 6 节三种降级场景各跑一次，确认产物合法且不崩溃。
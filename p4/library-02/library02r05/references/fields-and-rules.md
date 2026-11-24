# 字段与规则说明（fields-and-rules）

本文件定义脚本产物的字段语义与判定规则，供 Agent 语义层与评审复核对齐。

## 1. result.json 顶层字段

| 字段 | 类型 | 说明 |
|-|-|-|
| `case_id` | string | 从 case.json（case_id/caseId/challenge_id/id/armiId）或 task.md 的 challenge_id 识别；缺失则空串并置 `status=input_error` |
| `status` | string | `ok`/`partial`/`insufficient_evidence`/`input_error`，诚实标注 |
| `sources` | list | 每条 `{item, locator, quote}`；locator 为文件相对路径或 `path#pN`；quote 为逐字原文 |
| `uncertainty` | list | 每条 `{item, reason, severity}`，severity ∈ high/medium/low |
| `change_log` | list | append-only，`{time, actor, step, detail}`，与 operation_log.jsonl 逐条一致 |
| `errors`/`degradations` | list | 致命错误 / 可降级处理项 |
| `limitations`/`pending_human` | list | 方法边界 / 待人工复核项 |
| `runtime` | object | skill_version、elapsed_seconds、input_root、output_dir |
| `computed` | object | 全部机械证据（见下） |
| `artifacts` | list | 产物绝对路径清单 |

契约字段按 output_contract 递归回填后与上述字段并列；若契约键与保留键冲突，
改存 `computed._contract_<字段>`，并在 `computed.contract_coverage` 记录。

## 2. computed 子结构

- `summary`：文件/目录/空目录/符号链接/断链/硬链接组/降级计数。
- `files`：逐文件 `{relpath, name, ext, size, mtime, mode, type, symlink_target,
  inode, nlink, sha256, broken, error}`。
- `duplicate_groups`：`{sha256, count, files}`，按内容 SHA-256 分组，仅 count>1。
- `version_candidates`：`{stem, heuristic:true, members:[{relpath, size, sha256,
  version_hint, similarity_to_first}]}`。
- `aliases`：`{symlinks:[...], hardlinks:[{inode, files}]}`。
- `reconciliation`：`{matches, mismatches, missing, orphaned, counts, catalogs}`。
- `answers`：机械问答数组，每条 `{q, a, evidence}`。
- `index`：`{terms:{词→路径}, metadata:{by_ext}, truncated}`。
- `catalogs`：库目录解析结果（kind ∈ json/csv/markdown/bibtex/ris/sqlite）。
- `contract_coverage`：契约字段回填状态。

## 3. 判定规则

- **去重**：内容 SHA-256 相同 → 同一 duplicate_group；不按文件名。
- **版本**：文件名主干做 NFKC 归一化 + 小写 + 分隔符折叠，再用正则剥离
  `v1/v2/rev/final/draft/copy/副本/修订/终稿/草稿/扫描/scan/old/new/bak/backup/
  orig/original/最新/旧/dated-YYYY...` 等版本标记，得到归一化主干；同主干 ≥2 个
  文本类文件按 difflib.SequenceMatcher 计算内容相似度。**启发式，必须标注。**
- **别名**：`os.path.islink` 识别符号链接（含断链）；`st_nlink>1` 且同
  `(st_dev, st_ino)` 识别硬链接组。
- **对账**：库目录/清单中登记的路径（`path`/`file`/`relative_path`/文件/路径列）
  与盘点路径求交集/差集；`orphaned` 可能是"清单格式未识别"而非真实缺失，
  需标注不确定性。

## 4. 文本与哈希约束

- 单文件内容哈希与文本抽取上限 `MAX_CONTENT_BYTES = 2MB`，超出标记 `too_large`。
- 文本解码顺序 `utf-8-sig → utf-8 → gb18030 → latin-1`，最后 `errors=replace`。
- 相似度比对取前 `MAX_TEXT_CHARS = 20000` 字符。
- 索引词条上限 `MAX_INDEX_TERMS = 5000`，超出置 `truncated=true`。

## 5. 确定性约定

- 目录遍历 `os.walk` 前对 `dirnames`/`filenames` 排序；字典按插入序构建；无随机数。
- 同一输入重复运行，除 `time`/`elapsed_seconds` 等时间戳外，输出字节级一致。
- 源文件全程只读，不移动、不覆盖、不删除。

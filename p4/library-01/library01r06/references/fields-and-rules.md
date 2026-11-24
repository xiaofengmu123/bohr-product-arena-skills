# 字段字典与规则说明

本文件是脚本默认字段字典与方法约定的参考，供 Agent 在"语义归位"阶段使用；正式案例以 `/input/task.md` 与 `/input/case.json` 的规则为准，脚本按规则覆盖默认值。

## 六类要素（题集要求的覆盖项）

| 要素 | 默认字段/处理 | 落盘位置 |
|---|---|---|
| 文件类型 | `type`: file/dir/symlink/non_regular/unreadable | `inventory.json`、`library_manifest.csv` |
| 元数据字段 | `size`、`mtime`、`mode`、`inode`、`nlink`、`sha256` | `inventory.json`、`library_manifest.csv` |
| 目录层级 | 递归目录树，空目录标 `[空目录]` | `directory_tree.txt` |
| 重复文件 | 按内容 SHA-256 分组 | `dedup_version.json`、`library_model.json` |
| 版本差异 | 归一化主干 + mtime 排序 + 内容差异候选（启发式） | `dedup_version.json`、`library_model.json` |
| 权限与来源 | `mode` 八进制、`role`(control/material)、链接目标、清单来源 | `inventory.json`、`recovery_report.json` |

## 版本候选的归一化规则（启发式，非定论）

1. 取文件名主干并小写，去掉扩展名。
2. 去除版本标记：`v/ver/version/rev/r` + 数字。
3. 去除状态词：`final/draft/old/new/latest/bak/backup/copy/副本/初稿/终稿/旧/新`。
4. 去除复制计数 `(2)`、日期 `YYYY-MM-DD`，再去除非字母数字。
5. 同主干 ≥2 个文件即构成候选链，按 mtime 升序；内容哈希相同标记 `identical_content`。

该规则可能把"同名不同文档"误并为一条链，故 `confidence=heuristic` 并写入 `uncertainty`。

## 恢复对账的判定规则

- `matches`：清单条目路径或哈希命中库存，且哈希一致（或清单无哈希）。
- `mismatches`：清单路径命中库存，但清单哈希与库存哈希不一致。
- `missing`：清单条目的路径与哈希在库存中均未命中。
- `orphaned`：库存中的 material 文件未被任何清单条目引用。

## 检索索引

- 路径 token：文件名/目录名/哈希前缀 → 文件相对路径集合。
- 内容 token：文本类扩展名（≤1MB）的 ASCII 词 + CJK 二元组 → 文件相对路径集合。
- 每文件内容 token 上限 4000 个，防止索引膨胀。

## 状态语义

- `ok`：完成且无扫描错误、有 material 文件。
- `partial`：有扫描错误/写盘降级/契约缺失但已有可用结果。
- `insufficient_evidence`：无 material 文件或无法形成结论。
- `input_error`：case.json 缺失或不可读。

## 证据 locator 约定

- 文件事实：`相对路径`（如 `materials/raw/a.txt`），quote 为 `sha256=… size=… mode=…`。
- 任务规则：`task.md`，quote 为逐字行。
- 行政版本说明：`task.md`，quote 为逐字行。

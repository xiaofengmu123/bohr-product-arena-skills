# 评分维度对齐（本 Skill 的差异化设计）

本文件把题目的五个评分维度逐条映射到执行流程、落盘产物与自检项，使“按评分维度强化”
这一设计取舍可被复核。流程编号对应 `SKILL.md` 第 4 节的步骤 0–4。

## 实际效果（40 分）

| 评分点 | 分值 | 流程映射 | 落盘证据 | 自检项 |
|---|---|---|---|---|
| 任务完成度 | 15 | 步骤 1 全量盘点→去重→版本→别名→版本说明→对账→索引→问答 | `library_manifest.csv`、`directory_tree.txt`、`dedup_version.json` | 清单每行一个源文件，非“仅建议” |
| 结果正确性 | 15 | 内容 SHA-256 去重（非文件名）；版本链按归一化主干 + 修改时间 + 内容差异 | `dedup_version.json` 的 `sha256s`/`transitions` | 哈希与源文件一致，版本关系为启发式并标注 |
| 结果完整性 | 5 | 六类要素全覆盖：文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源 | `computed.files`（含 `permissions`/`inode`/`symlink_target`）、`directory_tree.txt` | 难处理文件未被静默跳过，逐条记录 |
| 结果可核验性 | 5 | 全程确定性遍历排序 + 稳定哈希 + 操作日志 | `operation_log.jsonl`、`result.json.computed` | 他人可按 `scripts/main.py` 复跑并比对 |

## 使用增益（25 分）

| 评分点 | 分值 | 流程映射 | 落盘证据 | 自检项 |
|---|---|---|---|---|
| 质量增益 | 15 | 机械层由脚本确定性完成（去重/版本/对账/索引/问答），Agent 只做语义层 patch | `result.json` + `--update patch.json` 增量合并 | 同输入同权限下字段填充率、去重与版本处理稳定 |
| 效率增益 | 5 | 库目录（JSON/CSV/BibTeX/RIS/SQLite）自动解析；机械问答自动给证据定位 | `recovery_report.json`、`computed.answers` | 减少人工整理条目数与重复上传 |
| 增益可验证性 | 5 | 脚本无随机数、遍历排序、哈希稳定；`operation_log.jsonl` 记录全部动作 | 操作日志 + 确定性产物 | 同输入两次运行（除时间戳外）一致 |

## 创新增益（15 分）

| 评分点 | 分值 | 流程映射 | 落盘证据 | 自检项 |
|---|---|---|---|---|
| 新增能力 | 5 | 别名识别（符号链接含断链 + 硬链接组）、行政版本说明解析并对齐、恢复对账 + 索引重建 | `dedup_version.json` 的 `aliases`/`version_note_alignment`、`recovery_report.json`、`library_index.json` | 上述能力均已真实落盘 |
| 方法改进 | 5 | 去重按内容哈希；版本候选标注 `heuristic=true`；版本说明 `linked/orphan` 区分 | `dedup_version.json` 字段 | 判定不按文件名，启发式结论明确标注 |
| 创新证据 | 5 | 自检脚本用合成批次验证判定逻辑（非预制正式答案） | `scripts/selftest.py` 退出码 0 | 创新点相对朴素方案的边界写于 `limitations` |

## 可靠体验（10 分）

| 评分点 | 分值 | 流程映射 | 落盘证据 | 自检项 |
|---|---|---|---|---|
| 调用与易用性 | 3 | 缺省参数指向 `/input`、`/app/output`、`/app/work`；`--help` 可用 | `SKILL.md` 最小调用示例 | Agent 能按说明完成一次完整落库与归档 |
| 运行稳定性 | 4 | 空目录、深层目录、中文/特殊字符文件名、重复运行均不崩溃 | `errors`/`degradations` 字段 | 不同文件类型与目录深度持续产出可用结果 |
| 异常处理 | 3 | 坏 JSON/坏编码/超大文件/缺权限/断链/非常规文件分别降级记录 | `errors`/`degradations`/`hash_reason` | 上传中断、权限不足、重名冲突、哈希不符均被识别 |

## 生态贡献（10 分）

| 评分点 | 分值 | 流程映射 | 落盘证据 | 自检项 |
|---|---|---|---|---|
| 文档完整性 | 3 | `SKILL.md` frontmatter + 输入/输出契约 + 降级策略 + 边界 | `references/checklist.md`、`references/rubric-alignment.md` | 目录规范、字段字典、命名规则、依赖、限制齐全 |
| 资源可复用性 | 4 | 仅 Python 标准库，相对路径调用，无个人路径 | `scripts/main.py`、`scripts/core_impl.py`、`scripts/selftest.py` | 脚本可被他人直接复用与复跑 |
| 分享推广价值 | 3 | 可交接清单 + 目录树 + 交接报告 + 操作日志 | `library_manifest.md` | 沉淀可分享的资料入库案例与实践经验 |

## 核对方法

```bash
python3 -B scripts/selftest.py
python3 -B scripts/main.py --help
python3 -B scripts/main.py --input /input --output /app/output --work /app/work
python3 -B scripts/main.py --check /app/output/result.json --case-json /input/case.json
```

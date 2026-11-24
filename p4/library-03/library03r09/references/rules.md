# 规则与字段映射（可复用）

本文是 `library-organizer` 的判定规则表，供他人复核与复用，不硬编码进单一脚本。所有规则产出都可经 `locator+quote` 或 SHA-256 复核。

## 1. 目录层级

- 默认 `--hierarchy document_type,topic`，即 `library/<document_type>/<topic>/`。
- 可按 task.md 调整维度组合（如 `document_type,year`），但首个维度必须是可在文件名/正文/BibTeX 中核验的强信号。
- 跨主题文件取命中次数最高的主题词；无法命中归 `general`；完全不可判归 `other`。

## 2. 文件类型判定（document_type）

| 类型 | 信号（命中一个即可，优先级自上而下） |
|---|---|
| dataset | 扩展名 .csv/.json/.xlsx/.xls |
| scanned | 扩展名 .png/.jpg/.jpeg/.tif/.tiff/.gif/.bmp |
| bibliography | 扩展名 .bib/.ris |
| note | 扩展名 .txt/.md |
| supplementary | 文件名/前 4KB 文本含 supplement/supporting/SI/appendix |
| preprint | preprint/arXiv/bioRxiv/medRxiv/chemRxiv/SSRN/tech-report |
| conference_paper | conference/proceedings/ICML/NeurIPS/ICLR/CVPR/AAAI/ACL 等 |
| thesis | thesis/dissertation/Ph.D/master-thesis |
| journal_article | 其余 .pdf（默认） |
| other | 无法判定的其它文件 |

## 3. 元数据字段映射

| 字段 | 首选来源 | 次选来源 | 缺失处理 |
|---|---|---|---|
| title / authors / year / journal / doi / school | 与文件相符的 BibTeX `@type{...}` 条目（按 DOI 或归一化标题匹配，避免张冠李戴） | 正文/文件名正则（DOI `10.xxxx/...`、arXiv `arXiv:xxxx.xxxxx`、四位年份、期刊线索） | `null`，进入 `pending_human` |
| topic | 标题+摘要关键词词表 `TOPIC_MAP` | 文件名 | `general` |
| tags | decisions.json 显式标签 | `[document_type, topic]` 归一 | `[]` |
| version_kind | 原文标记 | 文件名标记（vN/preprint/published/supplementary/revision） | `unknown` |

**证据规则**：每个非空字段至少一条 `{locator, quote}`，quote 为原文逐字片段；无证据字段不采纳，写 `pending_human`。冲突信息两处记录并标 `source_conflict`，不隐藏。

## 4. 命名模板

`{第一作者姓}-{年份}-{短标题}.{ext}`：

1. 特殊字符 `\ / : * ? " < > |` 及控制字符替换为空格；连续空格合并；首尾 `.` ` ` 去掉。
2. 标题超 50 字符截断；作者姓超 40 字符截断；无年份写 `nodate`；无标题用 `topic` 兜底。
3. 缺作者且缺年份时不造 `nodate-…` 名，回退为清洗后的原文件名（`untitled` 兜底），绝不保留会破坏路径的字符。
4. 同名不同内容自动 `-2/-3` 后缀，绝不覆盖。

## 5. 去重与版本区分

- **字节级重复**：SHA-256 相等 ⇒ 保留最短路径者，其余 `duplicate_of` 指向保留件、`action=skip_duplicate`。
- **版本组**：并查集按 `doi`、`arxiv_id`、`归一化标题+年份` 聚合，`version_kind ∈ preprint/published/supplementary/revision/unknown`；无原文标记时 `unknown`，不臆断哪份是正式版。
- **冲突**：同名不同 SHA-256 ⇒ `failures.conflicts` 记 `-N` 后缀解决过程；不同源两处记录，不删除。

## 6. 幂等与回滚

- 同一输入 + 同一 `decisions.json` 连跑两次，除时间戳外 `catalog`/`operations` 逐字节一致。
- `--apply` 才复制到 `/app/output/library/`，绝不修改 `/input`；重复 `--apply` 已存在同内容目标不重复复制。
- `rollback.json` 列出本次创建的全部目标文件，删除即可回滚；干跑时确认「未修改任何文件」。

## 7. 失败场景 → 字段映射

| 场景 | 写入字段 | status |
|---|---|---|
| 不可读 / stat 失败 | `failures.permission_errors` + `errors` | partial |
| 坏编码（替换字符） | 文件条目 `errors` + `degradations` | partial |
| 加密 PDF / 无文本层 | `has_text_layer=false` + `failures.skipped` | partial |
| 中文乱码 | `degradations`，改 utf-8-sig 重读 | 视结果 |
| 同名不同版本 | `failures.conflicts` + `-N` 后缀日志 | 不误删 |
| 缺 pdftotext/OCR | `degradations`，保留清单与哈希 | 不影响清单 |
| decisions 无证据 | `pending_human`，字段不采纳 | partial |

## 8. 最小验收

```bash
cd /skills/weekly-skill
python3 -B scripts/main.py --input /input --output /app/output --work /app/work --apply
python3 -B scripts/selftest.py
python3 - <<'PY'
import json
r = json.load(open('/app/output/result.json', encoding='utf-8'))
assert r.get('case_id') and r.get('status') in {'ok','partial','insufficient_evidence','input_error'}
assert 'sources' in r and 'uncertainty' in r and 'change_log' in r
print('ok', r['status'], r['case_id'])
PY
```

> 示例仅验证接口，不冒充正式案例结果；正式结果以平台固定案例实际运行为准。
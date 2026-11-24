# result.json 契约与字段说明

`/app/output/result.json` 由账本纯函数派生。字段分三类：核心字段、题目
`output_contract` 回填字段、可选解释字段。

## 1. 核心字段（始终存在）

| 字段 | 类型 | 说明 |
|-|-|-|
| `case_id` | string | 案例标识，取自输入；无法识别时为 `case:<文件名>` 或 `unknown` |
| `status` | enum | `ok` / `partial` / `insufficient_evidence` / `input_error` |
| `results` | array | 候选文献，每条含 rank/title/authors/year/journal/doi/url/database/relevance/relevance_reason/score/evidence |
| `evidence` | array | 查询与来源追踪 | 
| `sources` | array | 去重后的来源串（URL/DOI） |
| `uncertainty` | object | missing_metadata / conflicts / coverage_gaps / notes |
| `pending_human` | array | 无法自动判断、需人工确认的项 |
| `change_log` | array | 时间序、单调 seq 的变更记录 |

## 2. 候选记录字段

每一条 `results[i]` 至少含：

- `rank`：稳定排序后名次（1 起）；
- `title`：论文标题（缺失为 null，随即进入 missing_metadata）；
- `authors`：作者数组；
- `year` / `journal` / `doi` / `url`：元数据，缺失标 null；
- `database`：来源库，小写归一；
- `relevance`：0–1 相关度（由调用方判断）；
- `relevance_reason`：相关依据一句话；
- `score`：确定性得分，见下；
- `evidence`：数组，每项含 `locator` + 逐字 `quote`，指回材料或检索返回。

元数据冲突不隐藏：`source_conflict` 记录字段名与双方取值。

被排除的记录（`excluded:true`，附 `exclusion_reason`）进入 `results` 之外的
`excluded` 清单，保留筛选依据，供 `coverage_gaps` 与 `sources` 引用。

## 3. 确定性打分

```
score = 0.60 * relevance + 0.20 * metadata_completeness + 0.20 * source_reliability
```

- `relevance`：调用方给出的 0–1 判断（无则 0）。
- `metadata_completeness`：title/authors/year/journal/doi/url 六个字段的填充率。
- `source_reliability`：按库映射，crossref/publisher=1.0，europepmc=0.95，
  paper_search=0.85，web_search=0.7，local_pdf/material=0.8，unknown=0.5。

重复运行确定性：候选按 `(-score, -year, title小写, cand_id)` 稳定排序。

## 4. status 判定

- `errors` 含解析级阻断且无候选 -> `input_error`；
- 无候选 -> `insufficient_evidence`；
- 存在 missing_metadata / conflicts / degradations / pending_human -> `partial`；
- 否则 -> `ok`。

## 5. output_contract 回填

`scripts/main.py parse` 会从 case.json 的
`output_contract` / `outputContract` / `output` / `schema` / `required_fields`
等处抽取契约键，`assemble` 保证这些键全部原样出现在 result.json 顶层：
未获得的键回填 `null` 或 `[]`，不改名、不删除；契约里的示例值不当作事实
数据，规避“看起来像结果”的填充。

## 6. 一处补充约定

`status_notes` 用来说明空结果、冲突、权限或接口异常；`limitations` 写
已知覆盖缺口；`errors` / `degradations` 记录解析与工具降级；`runtime`
记录 started_at 等时间轴信息。
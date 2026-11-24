# 自查清单与数据格式（academic-translation-bilingual）

## 1. 提交/交付前逐项勾选

### 产物完整性
- [ ] `/app/output/result.json` 存在、合法 JSON、UTF-8、可被 `json.load` 读取。
- [ ] 含 `case_id`，与 `/input/case.json` 一致。
- [ ] 含题目 `output_contract` 全部字段（缺省为 `deliverables` / `fact_source_map` / `version_boundary`）。
- [ ] 含 `status` / `sources` / `uncertainty` / `change_log`。
- [ ] `deliverables[].path` 指向的文件真实存在；Office/图类有渲染 PDF/PNG 预览并写入 `deliverables`。

### 事实与来源映射
- [ ] 每条数字、单位、术语、缩写、引用、图表编号、否定/限定关键论断都有 `source` + `locator` + `quote`。
- [ ] 未新增材料外事实；推断与原文明确区分（`note` 标"推断/待核"）。
- [ ] 存疑引用、找不到原文、术语冲突、数字不一致均已登记 `uncertainty`。

### 版本与边界
- [ ] `modifications` 每项含 `what`（改了什么）/`why`（为什么）/`before_after`（差异）。
- [ ] 未确认项进 `human_confirm`；材料缺口进 `material_gaps`；异常进 `anomalies`。
- [ ] `editable=true` 时工件是可编辑文本/Office，而非只读截图。

### 一致性（脚本）
- [ ] 已运行 `scripts/check_consistency.py` 并保留报告。
- [ ] error（引用找不到、段编号重复、缺 src）全部处理或交人工确认。
- [ ] warning（术语多写法、数字疑似不一致、缺 tgt）已处理或登记。

## 2. 已知失败模式与处置

| 现象 | 原因 | 处置 |
|-|-|-|
| 术语每次重译、图表题注与正文不一致 | 未建 glossary | 先抽术语锁定 `canonical`，全篇与题注统一 |
| 数字被四舍五入/单位错配 | 未逐段核对数字 | 按 SEG 对齐逐段跑 `check_consistency.py`，保留原文原值 |
| 引用 [n] 对不上参考文献 | 未核对引用-文献配对 | 按报告补齐或登记存疑引用 |
| 译文凭空补写结论 | 未守语义边界 | 每条论断进 `fact_source_map`，无原文则不写 |
| 双语对照破坏段落顺序 | 未用固定 SEG 对齐格式 | 用约定格式逐段对齐，重跑 alignment 检查 |
| 中途崩溃全丢 | 未先落盘骨架 | 前 3 分钟 `main.py --init` |

## 3. glossary.json 格式

```json
{
  "terms": [
    {
      "canonical": "卷积神经网络",
      "src": "convolutional neural network",
      "variants": ["CNN", "卷积网络"],
      "note": "全文统一译作“卷积神经网络”；首次出现写“卷积神经网络（CNN）”；图表题注同用"
    }
  ]
}
```

规则：`canonical` 为全文统一译法；`variants` 列出同义/缩写；`src` 为原文对应表达（用于溯源）。同一英文词在不同学科译法不同时，在 `note` 注明学科语境。脚本 `check_consistency.py` 会扫描 `/app/output` 下的 `.md/.txt`：若 `canonical` 未出现而变体出现，或同术语出现多种写法，会报 warning。

## 4. 双语对齐稿格式约定

每个句段一个块，块头 `### [SEG-01]`（两位数字、连续编号），块内用列表项：

```markdown
### [SEG-01]
- src: The model achieved 95.3% accuracy (n=1024) [1].
- tgt: 该模型达到 95.3% 的准确率（n=1024）[1]。
- src_loc: paper.txt:12
```

- `src`：原文句段（可含公式、引用标记、脚注标记，原样保留）。
- `tgt`：译文句段（公式、数字、引用标记、否定/限定词原样落地）。
- `src_loc`：来源定位 `文件:行号`（PDF 可写 `文件:页码`）。

`check_consistency.py` 读取此格式做：数字逐段核对、段编号连续性、src/tgt/src_loc 完整性、否定/限定标记覆盖率（info 级，仅作人工复核线索）。

## 5. fact_source_map 条目模板

```json
{
  "item": "准确率 95.3%",
  "kind": "number",
  "value": "95.3%",
  "source": "paper.txt",
  "locator": "paper.txt:12",
  "quote": "achieved 95.3% accuracy (n=1024)",
  "note": "原文原值，未四舍五入；n=1024 一并保留"
}
```

`kind` 取值：`term`（术语）、`number`（数字/单位/量级/范围/年份）、`citation`（引用）、`figure`（图表/公式/脚注编号）、`claim`（关键论断）。每条必须 `locator`+`quote`，否则视为未完成。

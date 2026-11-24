# 文献研究质量评价量表（rubric）

本量表是评价的唯一事实标准：agent 必须逐维度、逐条目对照原文判定，每条判断绑定 locator+quote；禁止把摘要改写为“判断”。

## 1. 评价条目结构
```json
{
  "dimension": "study_design|evidence|bias|reporting",
  "checkpoint": "该条目名",
  "critical": true,
  "verdict": "见 §5 值域",
  "judgment": "一句话判断，必须由 quote 支持",
  "locator": "<源ID>#<锚点>",
  "quote": "原文逐字片段",
  "basis": "quoted|inferred|not_reported"
}
```
- `basis=quoted`：quote 直接支持判断；`inferred`：判断由 quote 之外的上下文推出（须在 judgment 说明推理）；`not_reported`：原文未报告 → verdict 必须为 `insufficient_evidence`/`not_reported`，quote 留空。

## 2. 证据定位语法（locator）
- `<源ID>#pN`：第 N 段；`#pNsM`：第 N 段第 M 句；`#pN-M`：第 N..M 段；`#raw:OFF:LEN`：规范化全文字符切片。
- 源ID 对应 `result.json.text_index` 的键（如 `S1` → `text/S1.txt`）。
- 句子切分为启发式（缩写/小数可能干扰）；`verify` 对 `pNsM` 先按句查、查不到回退整段，避免误报。quote 与原文空白折叠后逐字比对。

## 3. 研究设计分类（选择特有检查点）
| 类型 | 识别特征 | 特有偏倚域 |
|-|-|-|
| 干预实验（RCT/准实验） | randomiz/trial/arm/对照 | 随机化、分配隐藏、盲法、失访、选择性报告 |
| 队列研究 cohort | follow-up/暴露组/incidence | 选择偏倚、失访、混杂、信息偏倚 |
| 病例对照 case-control | case/control/retrospect | 选择偏倚、回忆偏倚、混杂 |
| 横断面 cross-sectional | 单时点/survey/prevalence | 抽样、测量、时序 |
| 系统综述/荟萃 SR/MA | systematic review/meta | 检索完整性、纳入标准、发表偏倚、异质性 |
| 诊断/预测模型 | sensitivity/ROC/validation | 谱偏倚、金标准、盲法、过拟合 |
| 无法分类 | — | 用通用检查点，`limitations` 标注 `unclassified_design` |

## 4. 四类核心维度与检查点

### 4.1 study_design（研究设计）
- SD1 研究类型与问题匹配（critical）
- SD2 对照/比较组设置
- SD3 暴露—结局时序（观察研究必查）
- SD4 设计对因果/关联结论的支持程度（critical）

### 4.2 evidence（证据充分性）
- EV1 样本量与抽样方法（critical）
- EV2 测量与结局定义清晰、可复现
- EV3 统计分析与效应量/置信区间/缺失值处理
- EV4 数据/代码/注册可获取性

### 4.3 bias（偏倚风险，四等级：present/suspect/absent/insufficient_evidence）
按 §3 选偏倚域，逐域判定其“具体表现”，不写笼统结论：
- BI1 主要偏倚域是否存在（critical）
- BI2 控制/校正措施
- BI3 失访/脱落与处理
- BI4 选择性报告/发表偏倚

### 4.4 reporting（报告完整性）
- RP1 报告清单符合度（CONSORT/STROBE/PRISMA/STARD 按类型选）
- RP2 关键方法/结果缺项
- RP3 图表/数据可解析性
- RP4 利益冲突/资助/注册号

## 5. 判定值域（verdict）
- study_design / evidence / reporting：`adequate | partial | inadequate | insufficient_evidence | not_applicable`
- bias：`absent | suspect | present | insufficient_evidence | not_applicable`
- 区分“未见迹象”（`absent`，有证据支持未见）与“证据不足”（`insufficient_evidence`，原文没提）——禁止混用。

## 6. 分级阈值（grade）
`scripts/litqa.py grade` 的聚合规则（显式、可复现）：
- 风险分值：`present`/`inadequate`=2，`suspect`/`partial`=1，`absent`/`adequate`=0，`insufficient_evidence`/`not_reported` 计 0 但 uncertainty+1。
- 等级：任一 `critical` 项 present/inadequate，或 risk_points≥4 → `high`；≥2 → `moderate`；存在 insufficient_evidence/not_reported 且其余无风险 → `uncertain`；否则 `low`。
- 理由写入 `grade.rule` 与各 critical 项判定，不孤立给标签。

## 7. 弃权规则
- 原文未报告 → verdict=`insufficient_evidence` 或 `not_reported`，basis=`not_reported`，quote 留空（verify 记为 not_applicable）。
- 不得用“样本量适中”“设计合理”等无证据措辞补缺；不得虚构 DOI、页码、数值。
- 全文不可得/仅摘要/图表不可解析/预印本或语言特殊 → 逐项写 `limitations`，并降低对应维度 status。

## 8. 检索与筛选输出
- `candidates`：候选文献 {id, title, authors, year, venue, doi, uri, type, access, relevance}，元数据只写输入中真实存在的。
- `screening`：每条 {decision: include|exclude|pending, reason, locator, quote}，保留筛选依据。
- `omissions`：检索范围缺口、未覆盖数据库、权限/接口异常；候选集为空时 status=insufficient_evidence。
- `sources`：每条 {id, type, path_or_uri, locator, quote, note}，type ∈ {paper, dataset, response, note}。

# 六维结构诊断规则

## 1. 六维分类（每条判断必须归类，缺项说明非主要问题）

| 维度 | 诊断对象 | 典型问题 | 对应编辑动作 |
|-|-|-|-|
| research_question | 研究问题是否清楚提出、贯穿全文，贡献主张与实际工作是否一致 | 问题与方法各说各话；贡献超出实际完成的工作 | 重述问题；收窄贡献主张；在引言/结论处对齐措辞 |
| section_structure | 章节顺序、篇幅分配、信息释放顺序是否服务论证 | 该前置的铺垫缺失；该后移的细节提前；应合并的章节分散 | 移动小节；合并章节；拆分过载章节；调整篇幅 |
| argument_chain | 逐条论断与支撑论据的对应 | 断裂、跳跃、循环论证、论据强度不足 | 补论据引用；重排论证步骤；拆分或合并论断 |
| paragraph_flow | 段落间过渡、衔接、同义/跨章节重复 | 同义重复；跨章节重复表述；缺过渡或逻辑跳跃 | 合并重复段；拆分段；补过渡句；删冗余 |
| conclusion_boundary | 结论是否超出数据与实验条件范围 | 相关表述为因果；缺限制说明；过度泛化 | 收窄结论；补限制；改因果为相关；限定样本/条件 |
| citation_integrity | 引用/数字/术语来源一致性 | 引用找不到；数字无来源；术语前后不一致 | 补来源；对齐术语；标注无文献支持 |

若某维度无问题，在 diagnosis 中写"未发现明显问题"并给一句证据，不硬凑缺陷。

## 2. 四个核心问题的映射

- **诊得准** → `claim` + `dimension` + `locator`：定位到章节/段落/句子，并给问题类别。
- **改得动** → `edit`：写目标位置与操作（移动/合并/拆分/收窄/补限制），附 `original` 与 `proposed` 片段，不另起整篇改写。
- **链得通** → `basis` + `quote`：每条论断有论据；跨章节呼应写 `related_claim_id`。
- **说得清** → `verdict`（`confirmed | needs_human | rejected`）+ `confidence`，作者可判定采纳/部分采纳/驳回。

## 3. evidence_map 字段约定（细粒度）

```json
{
  "claim_id": "c1",
  "dimension": "conclusion_boundary",
  "claim": "结论把相关表述为因果",
  "locator": "paper.md#Conclusion:L3",
  "quote": "therefore the method causes better outcomes",
  "basis": "Results 仅报告相关性 (n=100)",
  "related_claim_id": null,
  "original": "causes better outcomes",
  "proposed": "is associated with better outcomes under the tested conditions",
  "edit": "把结论动词收窄为关联表述，并补充样本与条件限制",
  "confidence": "high | medium | low",
  "status": "confirmed | source_conflict | not_reported",
  "verdict": "needs_human"
}
```

- `quote` 必须是原文逐字片段，可省略中缀用 `…`；`locator` 写"文件 + 章节 + 行号"。
- 冲突信息不隐藏：同一指标两个来源不一致时，两条都记录，`status=source_conflict`。
- 缺失证据写 `not_reported`，不补造引用/DOI/数值。

## 4. 判断语气与证据强度

- `high`：原文可直接支持，无需推断。
- `medium`：需要一次明确推断，依据写清。
- `low`：猜测或待确认，必须进 `pending_human` 或 `uncertainty`。
- 结构缺陷与风格偏好区分：只有影响论证闭环、可执行定位、或事实/来源一致性的才算结构问题；仅用词偏好不记为缺陷。

## 5. 结果写法

- `diagnosis`：按六维分节，每节列 claim_id，正文写"问题—依据—建议"三要素，连续段落。
- `edit_plan`：逐条对应 claim_id，写动作、目标位置、original/proposed、优先级（P0/P1/P2）。
- `version_boundary`：记录修改差异（哪些是建议、哪些已直接改稿）、人工确认项、材料缺口与异常。
- 无参考文献、图表公式缺失、稿件截断、格式混乱：写入 `uncertainty` 并降级，不虚构诊断。

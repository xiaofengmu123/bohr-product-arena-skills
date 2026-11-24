# 结构问题分类与可执行编辑动作规范

本文件是 SKILL.md 第 4 节"六维诊断"的执行细则。诊断结论必须落到这里定义的问题类型与动作，禁止用泛化写作原则代替定位判断。

## 1. 六维诊断维度与问题类型

### D1 研究问题与贡献主张对齐
- `D1.1 研究问题缺失/模糊`：引言未见可证伪的问题句；贡献句与问题无对应。
- `D1.2 贡献主张越界`：宣称完成了方法/实验并未支撑的工作（如"首次""完全""普遍"）。
- `D1.3 问题—方法—结论脱节`：方法未服务于问题，结论回答的是另一个问题。

### D2 章节结构与叙事顺序
- `D2.1 信息释放顺序不当`：读者先遇到未定义的术语/背景；铺垫应在核心论证前。
- `D2.2 篇幅失衡`：非核心细节占用过多篇幅，核心论证被压缩（用 `chapter_model.json` 的 chars 占比佐证）。
- `D2.3 章节功能错位`：方法细节散落在结果，讨论被写成结果复述。

### D3 论证链与论据对应
- `D3.1 论断无据`：断言句其后没有对应数据/引用/图表演示支撑。
- `D3.2 论据强度不足`：用单点/小样本/相关数据支撑强因果或强推广结论。
- `D3.3 循环论证`：结论与其前提互为支撑，缺少独立证据。
- `D3.4 跳跃推理`：从 A 直接到 C，缺 B 环节，且无过渡句说明。

### D4 段落关系与重复内容
- `D4.1 同义重复`：同一论断在同章/跨章以不同措辞出现（用 `redundancy.json` 的 jaccard 佐证）。
- `D4.2 段落断裂`：相邻段落主题突变、无过渡句。
- `D4.3 段落臃肿`：一段包含多个不同论点的论据，应拆分。

### D5 结论边界与表述尺度
- `D5.1 相关性表述为因果`：仅相关证据却写"导致/引起/使得"（用 claim_model 的 causal 标记佐证）。
- `D5.2 遗漏限制`：无样本量、条件、测量、外部效度等限制说明。
- `D5.3 过度推广`：把特定对象/条件结论推广到全类，无边界限定。

### D6 其他结构缺陷（非穷尽）
- 缺摘要/缺参考文献/缺图表编号、图表与公式缺失、稿件被截断等，如实标注，不计入五类核心维度时说明原因。

## 2. 诊断定位（说得清）

每条 finding 必须含：
- `locator`：`文件#P段落[.S句子]` 或 `文件#H标题`（来自 text_model.json 的稳定 ID）。
- `quote`：原文逐字片段（可截断，用 `…` 标省略）。
- `dimension`：上述 D1–D6。
- `problem_type`：如 `D3.1`。
- `severity`：`critical`（阻断论证闭环）/ `major`（影响某节论证）/ `minor`（风格偏好或可留待人工）。

判定规则：只有能给出 locator+quote 的问题才写入 finding；找不到原文依据的，不得虚构缺陷。

## 3. 可执行编辑动作（改得动）

每条动作是可执行的单一编辑指令，作者无需重写即可对照执行：

| action_type | 含义 | 必需字段 |
|-|-|-|
| `move` | 移动到哪 | from_locator, to_locator, target_text |
| `merge` | 与哪段合并 | from_locator, to_locator, merged_note |
| `split` | 从哪里拆分 | target_locator, split_after, split_point |
| `narrow` | 哪句论断需要收窄 | target_locator, original_claim, narrowed_claim |
| `insert_transition` | 补衔接/过渡句 | target_locator, direction(before/after), suggested_text |
| `add_evidence` | 补论据或标注缺据 | target_locator, missing_evidence |
| `move_detail` | 该后移的细节 | from_locator, to_locator, detail_text |
| `rename_section` | 章节功能改名 | target_locator, old_title, new_title |

动作状态：`proposed`（建议）/ `accepted`（采纳）/ `partial`（部分采纳）/ `rejected`（驳回）。每条动作必须给 `rationale`（依据）与 `evidence_locators`（支撑该判断的原文位置），保证作者能判断采纳/部分采纳/驳回。

## 4. 论点—论据映射（链得通）

逐条抽取论断，建立 `claim_map`：每条 claim 记录 `claim_text`、`locator`、`quote`、`evidence_locators`（支撑它的数据/图表/引用位置）、`chain_status`：
- `supported`：论据存在且强度匹配。
- `weak`：论据强度不足（需收窄或补证据）。
- `broken`：无对应论据。
- `circular`：循环论证。
- `unverifiable`：原文无法定位。

跨章节呼应检查：结论/摘要中的论断必须能在结果或方法中找到对应论据；找不到即为 `broken` 或 `unverifiable`。

## 5. 边界与措辞对照

把 claim_model 中 `causal` 标记句与"本研究数据可支撑的关系"逐条比对：仅相关性证据时，将因果措辞改写为"与…相关/相关分析显示"，并写入 `narrow` 动作。`overclaim` 标记句改写为有条件的表述，并在 `version_boundary.diffs` 记录改前/改后。

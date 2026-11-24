# 降级、核验与异常处理规则

本文件规定材料缺失、软件缺失、冲突、超长等场景的诚实降级动作。核心原则：
**不编造**。缺失就写缺失，不确定就写不确定，绝不用文件名 / 领域标签补造数值、文献、
作者或政策口径。

## 1. 输入侧降级

| 场景 | 动作 |
|---|---|
| `/input` 目录不存在或为空 | `read` 照常产出 summary（case_id 为 null），`skeleton` 照常写 result.json，status=`insufficient_evidence` 或 `partial`；uncertainty 注明"无材料" |
| `case.json` 坏 JSON | 记录到 summary.errors，沿用默认 output_contract，继续处理其余材料 |
| `case.json` 缺 `output_contract` | 使用默认契约 `deliverable / facts_sources / version_boundaries`，在 errors 注明 |
| 文件超 2MB | 跳过该文件并在 errors 记录 `too_large`，uncertainty 注明"未覆盖" |
| 坏编码 | 以 `utf-8` + `errors=replace` 读入，errors 记录 `bad_encoding` |
| PDF 原文 | 不直接读二进制；用平台 `pdf-text-tool` / `pdf-render-tool` 解析后再引用，locator 指向解析结果段落 |

## 2. 写作侧降级

- **图表读数不清 / 缺统计量 / 单位不明**：不猜。数值留 `null`，`confidence=unverified`，
  写入 `uncertainty`，quote 只写能确认的片段。
- **引用找不到 / 无法核实**：保留原文位置，`status=unverified`，不伪造 DOI / URL。
- **同指标两处不一致**：两处都记入 evidence_map，`status=conflict`，正文取保守表述并在
  `version_boundaries` 说明。
- **趋势一致但未达显著、仅在高剂量组出现**：保持边界写法（"呈…趋势但未达显著"），
  `strength=weak`，不放大为结论。
- **机制推测**：标 `causal_boundary=speculative`，正文用"可能 / 推测 / 有待验证"。

## 3. 软件 / 交付降级

- 无 pdftotext、Office 渲染器、LaTeX 等：如实写 `limitations`，交付纯文本 / 结构化产物，
  不假装已渲染。可编辑 Office/幻灯片/图示必须附真实 PDF/PNG 预览，渲染不了就注明未渲染。
- 写盘失败：捕获异常，降级为最小合法 result.json，进程不崩溃；change_log 记录失败原因。
- 运行失败：status=`failed`，change_log 记录失败原因与已完成中间产物，result.json 仍须落盘。

## 4. 已完成 / 拟开展 / 人工确认（区分来源）

- `result_*` 条目：只写材料中已完成的观察（已完成）。
- `discussion_interpretation / causal_claim / speculation`：本研究的解释，须有 `basis`；
  超出本研究、依赖外部设计的因果句标 `pending_human`。
- `pending_human`：需要作者确认才能定稿的内容（样本量、盲法、统计模型选择、单位换算口径、
  缩写定义等）。
- `version_boundaries`：记录修改差异、人工确认项、材料缺口、异常，供后续继续编辑。

## 5. 核验规则（check 子命令口径）

- hard error：缺 case_id、status 非法、缺 output_contract 字段、sources/uncertainty/
  pending_human/change_log/evidence_map 非 list、evidence 条目缺 locator/quote、claim_type
  非法、讨论/因果条目缺 strength、因果/推测条目缺 causal_boundary 或 basis。
- warning：出现禁词、正文有数值但 evidence_map 无 `value` 覆盖、无 evidence_map。
- `check` 退出码：有 hard error 返回 1，否则 0；warning 不阻断提交，但必须回修或说明。

## 6. 重复运行一致性

脚本无随机数、无时间排序依赖（change_log 时间戳由调用方控制）；同一输入两次运行产出
结构一致。所有中间产物写 `/app/work`，最终产物写 `/app/output`。

## 7. 评审人视角自检与常见失败模式（对应质量增益 / 创新增益）

交付前以评审人视角逐条过一遍；每个失败模式都有对应的纠正动作与 check 校验。

| 四问 | 基础 Agent 常见失败 | 纠正动作 | check 拦截 |
|---|---|---|---|
| 读得准 | 把 SEM 当 SD；误差棒当显著性；忽略对数轴/截断轴/双 y 轴；单位擅自换算；有效数字乱加 | 按图表读数协议逐项核；误差棒含义不明标 `unreported` 写入 uncertainty | quote 逐字非空；数值 token 未覆盖 → warn |
| 分得清 | 结果段写"证明/导致/机制"；讨论段堆引用不讲关系；局限写成泛泛自评 | 结果段只陈述观察；讨论段每句带 basis；局限落到方法/样本/测量/统计 | 因果/推测条目缺 causal_boundary → hard |
| 说得住 | "趋势一致但未达显著"写成"显著升高"；小样本写成"证实"；相关写成因果 | strength 与证据匹配；未显著写方向不写结论；因果需设计支撑 | strength/causal_boundary 缺失 → hard；禁词扫描 |
| 核得了 | 数值无来源；引用找不到就编 DOI；不标 unverified/conflict | locator+quote 逐条；缺失标 pending_human；冲突并录两处标 conflict | 缺 locator/quote → hard；status 非法 → hard |

评审人三问（逐条在 change_log 记录结论）：

- 某句"该不该这么写"能否由 locator + quote 独立判断？——能，则通过"结果可核验性"。
- 语气强度是否与 `strength`、`causal_boundary` 一致？——一致，则通过"结果正确性"。
- 是否存在"目标三、路线二、指标另说"式脱节（此处指结果↔讨论↔文献↔局限）？——无，则通过"一致性"。

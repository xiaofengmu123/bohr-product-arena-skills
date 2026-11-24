# 理由码字典（reason_codes）

每条判定必须带一个 `reason_code`。纳入用 `in_*`，排除用 `ex_*`，不确定用 `unc_*`。
排除码必须指向标题/摘要中的具体判据，禁止只写“不相关”。

## 纳入 in_*

- `in_population`：研究对象符合纳入标准的人群/样本
- `in_intervention`：干预/暴露符合纳入标准
- `in_outcome`：结局指标符合纳入标准
- `in_design`：研究设计符合纳入标准
- `in_full_criteria`：全部纳入标准均满足且无排除情形

## 排除 ex_*

- `ex_wrong_population`：人群不符（如动物研究，而标准为成人）
- `ex_wrong_intervention`：干预不符
- `ex_wrong_outcome`：结局不符
- `ex_wrong_design`：设计不符（如个案报告，而标准为 RCT）
- `ex_not_relevant`：主题无关（必须补具体判据，如“研究的是 Z 而非 X”）
- `ex_language`：语种不符
- `ex_no_abstract`：仅有标题且标题不足以纳入
- `ex_duplicate`：重复记录（去重阶段处理）
- `ex_retracted`：已撤稿
- `ex_not_primary`：非原始研究（综述/评论/信）

## 不确定 unc_*

- `unc_boundary`：对象/干预部分重叠，需人工
- `unc_no_abstract`：摘要缺失，无法判定
- `unc_language`：语种存疑
- `unc_missing_metadata`：DOI/年份等元数据缺失
- `unc_source_conflict`：两个来源对同一记录信息冲突

## 冲突类型（conflict.type）

- `decision`：两名标注者 decision 不一致
- `reason_code`：decision 一致但 reason_code 不一致
- `evidence`：decision 与 reason_code 一致，但证据片段不一致

## 仲裁结论（arbitration）

- `arbitrated`：已给出仲裁结论（须记录依据）
- `uncertain`：无法仲裁，维持待定
- `pending_human`：需研究者确认

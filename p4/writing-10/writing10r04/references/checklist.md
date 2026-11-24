# 自检清单（对应评分维度）

## 实际效果（40 分）
- [ ] 把真实返修材料（意见清单+指定版本稿件）处理成可直接提交的修改稿与逐条回复信。
- [ ] 每条意见及其全部子要求都被识别并给出明确处理结论，无笼统致谢或只答一半。
- [ ] 回复中的事实、数据、引用和专业判断准确；每条"已修改"与稿件真实状态一致。
- [ ] 每条定位能在指定版本稿件中独立核对（anchor 可检索）。
- [ ] 每条论断可回溯到 `source_ref` + 逐字 `quote`，不新增材料外事实。

## 使用增益（25 分）
- [ ] 相比基础 Agent，意见拆解、逐条台账、anchor 定位与证据清单降低逐条比对、查找行号、重写语气的时间。
- [ ] 增益可验证：提供 `scripts/selftest.py` 可复跑，同输入下可对比加载/不加载 Skill 的结果，而非仅文字自述。

## 创新增益（15 分）
- [ ] 形成基础 Agent 难以稳定实现的能力：意见→子要求拆解、diff+anchor 定位、证据到位状态、显式语气字段、版本一致性台账。
- [ ] 方法实质改进：把"改得准、证得住、对得上"落地为机器可校验的不变量，而非自由文本建议。

## 可靠体验（10 分）
- [ ] 模糊请求下先索要稿件版本、格式、受众、周期；缺材料先列 `material_gaps`。
- [ ] 长稿件、多意见、多轮返修与重复运行下要素不漂移、版本号前后一致。
- [ ] 意见冲突/意见有误/稿件缺章节/无法补实验/版本不匹配/权限不足分别说明，不编造。

## 生态贡献（10 分）
- [ ] 使用说明、依赖（仅标准库）、输入模板、示例、边界与维护信息齐全。
- [ ] 意见分类法、回复信模板、定位脚手架、评分表可被他人直接复用。
- [ ] 沉淀可分享的返修台账格式、语气分级与不确定性标注方法。

## 执行前必查
- [ ] 已读 `/input/case.json` 与 `/input/task.md` 原文，未按文件名臆测内容。
- [ ] 3 分钟内已落盘 result.json 骨架（scaffold.py），status=in_progress。
- [ ] 每次修改追加 `change_log`，重跑 `check_revision.py`，最终跑 `selftest.py`。

## 单案例运行手册（runbook）

```bash
# 1) 骨架（必须在开始后 3 分钟内完成）
python scripts/scaffold.py \
  --case /input/case.json --task /input/task.md --materials /input/materials \
  --out /app/output/result.json --work /app/work

# 2) 意见拆解后写改稿与回复信，落盘 /app/output/<改稿>.md 与 <回复信>.md，
#    回填 result.json 的 deliverable.path / response_letter.path / entries / changes。

# 3) 一致性自检（"对不上、查不到、证不住"处据报告修正）
python scripts/check_revision.py \
  --result /app/output/result.json \
  --manuscript /app/output/<改稿>.md \
  --response /app/output/<回复信>.md \
  --work /app/work

# 4) 终态落盘：status=partial|complete，重跑第 3 步；最后 30 秒只做 validate。
```

三案例循环提示：每个案例独立跑一遍上述流程，`case_id` 不可串用；`change_log` 每条改动追加而非覆盖，保证重复运行可追溯。


# 确定性与可复跑规范

本包的核心设计取舍：**把“智能”与“机械”彻底分离**，用确定性保证同输入两次产物一致。

## 分层

| 层 | 由谁写 | 是否确定性 |
|---|---|---|
| `draft.json`（claim/role/evidence/quote） | Agent 真实阅读材料后手写 | 唯一非确定层，写完即冻结 |
| `presentation.pptx`（布局/母版/占位符） | `pptx_core.py` 机械生成 | 确定 |
| `result.json`（契约/证据映射/自检） | `main.py` 机械组装 | `--repro` 下确定 |
| `presentation.pdf` | office-renderer 导出 | 依赖渲染器，页数须一致 |

## 确定性实现

1. **固定 ZIP**：条目按名排序、`ZipInfo` 固定时间戳 `(1980,1,1)`、`ZIP_DEFLATED` 压缩级别 6、unix `create_system`、固定 `external_attr`——相同 parts 输出字节一致。
2. **固定版式选择**：role → 版式名偏好序固定；同 role 永远选中同一版式。
3. **固定几何与字体**：占位符几何取自版式，字号/行距写死，不指定 latin 字体（跟随主题），CJK 检测决定 `lang`。
4. **三重指纹**：`input_fingerprint`（输入文件哈希）、`draft_fingerprint`（draft 规范化哈希）、`output_fingerprint`（parts 哈希）——复跑时三者可独立比对。
5. **`--repro`**：时间戳与时长置固定值，使 result.json 也字节一致；默认模式保留真实运行时元数据（时间戳是唯一合法差异，其余内容字段仍确定）。

## 冻结纪律（保证两次运行一致的关键）

- `draft.json` 一旦写入即视为冻结：后续只允许追加/修正 `uncertainty`、`change_log`、补图，**不得改写已定稿页面的 claim/quote**。
- 增量为“重写 result.json + 追加 change_log”，而非重新生成内容层。
- 每次落盘都在 `change_log` 记录 `{ts, action, reason}`。

## 复跑验证

```bash
python3 -B scripts/main.py --input /input --work /app/work --output /app/output \
    --draft /app/work/draft.json --repro
python3 -B scripts/selftest.py --root . --input /input --work /app/work --output /app/output
# 期望：deterministic rebuild (draft -> same pptx parts)  = PASS
#       result.json output_fingerprint matches rebuild     = PASS
```

`selftest.py` 用同一 `draft.json` + 同一输入重新构建 parts，比较重建指纹与磁盘 `presentation.pptx` 指纹及 `result.json` 中记录的指纹；三者一致才算通过。缺 `draft.json` 时该检查标记为 SKIP，绝不伪造。

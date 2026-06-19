# 军理搜题工作流 v3

> 千问VL识图 → 六源交叉验证 → 考场终版答案

## 工作流

```
图片 → 千问VL识别题号 → 校对稿 → 你校对+贴AI
                                        ↓
                                pipeline API搜题
                                        ↓
                    crosscheck 六源(itihey+DS+GM+DB+wby+课本)
                                        ↓
                                  终版答案(🟢🟡🔴)
```

## 快速开始

```bash
pip install requests pillow pyyaml

cp config.example.yaml config.yaml
# 编辑 config.yaml，填入 itihey API Key + DashScope Key

# 跑 pipeline
python -m core.main config.yaml 测试N --questions temp/_reviewed_测试N.json

# 交叉验证
python -m core.crosscheck 测试N
```

## 模块

```
core/
├── search.py       # API搜索 + 多轮降级 + 匹错检测
├── crosscheck.py    # 六源交叉验证 + 三色判定
├── verify.py        # 核验(否定题/匹错/长答案)
├── output.py        # Markdown输出
├── fallback.py      # 本地题库/课本兜底
└── main.py          # 主流程
vision.js             # 千问VL识图
itihey_search.py     # 单题CLI
```

## 判定

| 🟢 | 题库+3AI全票 | 98% |
| 🟡 | 题库vs AI不一致，AI内部一致 | 85% |
| 🔴 | 各方不一致 | 60% |

## 重要

⚠️ "题库" = API声称命中，非100%可靠。🟡🔴题需人工确认。

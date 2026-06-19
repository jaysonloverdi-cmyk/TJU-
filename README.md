# 军理搜题工作流 TJU-Military-Exam

> 千问 VL 识图 → 六源交叉验证 → 考场终版答案  
> 两天从裸代码到生产级管线，120 题新题库

## 它能做什么

30 道军理题目的图片扔进去，5 分钟出一份经过六方验证的考场答案——itihey 题库（5.4亿条）、本地题库、课本、DeepSeek、Gemini、豆包交叉比对，🟢全票通过直接采用，🟡有分歧标出，🔴打架的让你人工裁决。

## 工作流

```
📷 图片 → 🤖 千问VL识别题号 → 📝 校对稿 → 👀 你校对+贴AI
                                            ↓
                                    ⚡ pipeline API搜题
                                            ↓
                        🔀 六源交叉验证(itihey+DS+GM+DB+wby+课本)
                                            ↓
                                       📊 终版答案
                                    🟢全票 🟡分歧 🔴存疑
                                            ↓
                                     ✅ 入新题库
```

## 项目结构

```
├── core/
│   ├── search.py        # API搜索 + 多轮降级 + 匹错检测
│   ├── crosscheck.py     # 六源交叉验证 + 三色判定
│   ├── verify.py         # 核验(否定题陷阱/匹错/长答案)
│   ├── output.py         # Markdown表格生成
│   ├── fallback.py       # 本地题库/课本兜底
│   ├── ocr.py            # EasyOCR备用
│   └── main.py           # 主流程编排
├── itihey_search.py      # 单题CLI入口
├── vision.js              # 千问VL识图
├── wby题库.md             # 本地题库(113条)
├── 考试参考.md            # 课本OCR(20万字)
├── 新题库.md              # 交叉验证题库(120题)
├── config.example.yaml    # 配置模板
└── CLAUDE.md              # AI协作工作流文档
```

## 快速开始

```bash
pip install requests pillow pyyaml

cp config.example.yaml config.yaml
# 编辑 config.yaml: itihey API Key + DashScope Key

# 跑 pipeline
python -m core.main config.yaml 测试N --questions questions.json

# 交叉验证
python -m core.crosscheck 测试N
```

## 判定逻辑

| 🟢 | 98% | 题库 + 3AI 全票通过 |
| 🟡 | 85% | 题库 vs AI分歧，AI内部一致 → 采用AI共识 |
| 🔴 | 60% | 各方不一致 → 人工裁决 |

## 实测表现

| 测试 | 题库命中 | 全票通过 | 说明 |
|------|----------|----------|------|
| 测试1 | 26/30 | 22 | 83% |
| 测试3 | 20/30 | 17 | 67% |
| 测试5 | 28/30 | 21 | 93% |
| 模拟 | 28/30 | 26 | 93% |

## 重要

⚠️ itihey "题库"标签 = API 声称命中，非 100% 可靠  
⚠️ 🟡🔴 标记的题目请人工确认  
⚠️ 本地题库仅做兜底验证，不作为答案依据

# 军理搜题工作流

> 基于 itihey API 的批量搜题 + 自动答案生成系统

## 快速开始

```bash
# 1. 安装依赖
pip install requests easyocr pillow pyyaml

# 2. 配置
cp config.example.yaml config.yaml
# 编辑 config.yaml，填入 itihey API Key

# 3. 准备题目（两种方式）
# 方式 A：图片 → OCR 自动提取
python -m core.main config.yaml 测试1 --images 测试1/

# 方式 B：JSON 题目文件
python -m core.main config.yaml 测试1 --questions questions.json

# 单题快速查询
python itihey_search.py "克劳塞维茨是哪个国家的军事思想家？" "普鲁士|英国|法国" 0
```

## JSON 题目格式

```json
[
  {"q": "题目文本", "opts": "选项A|选项B|选项C", "type": 0},
  {"q": "判断题干", "opts": "对|错", "type": 3}
]
```

type: `0`=单选 `1`=多选 `2`=填空 `3`=判断 `4`=简答

## 模块架构

```
core/
├── search.py      # API 搜索 + SQLite 缓存 + 多轮降级 + 答案清洗
├── ocr.py         # EasyOCR 多 pass 提取 + 课本词库纠错
├── fallback.py    # 课本关键词搜索 + 本地题库搜索
├── verify.py      # 跨来源核验（✅⚠️❌ 三态）
├── output.py      # Markdown 表格生成 + 统计
└── main.py        # 主流程编排
config.yaml        # 配置文件（换课只需改此文件）
itihey_search.py   # 单题 CLI 入口（向后兼容）
vision.js           # 千问 VL 视觉识别（辅助 OCR）
```

## 搜题策略

1. **API 多轮降级搜索**
   - 完整题 + 选项 → 搜
   - 搜不到 → 去下划线/标点 → 再搜
   - 还搜不到 → 去选项搜 → 再搜
   - 选项关键词反搜

2. **课本兜底**（API 未命中时）
   - 关键词匹配 + 上下文提取

3. **本地题库兜底**
   - 文本相似度匹配

4. **自动核验**
   - 答案是否匹配选项
   - 答案长度是否异常
   - ⚠️ 标记需人工确认的题目

## 重要提示

⚠️ "题库"标签 = API 声称命中，未经人工验证。⚠️ 标记的题目请人工确认。

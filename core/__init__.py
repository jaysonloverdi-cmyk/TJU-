"""军理搜题工作流 — 核心模块包

模块：
- search:   API 搜题 + 模糊匹配 + 缓存
- ocr:      EasyOCR 多 pass 提取 + 纠错
- fallback: 课本搜索 + 本地题库搜索
- verify:   跨来源核验
- output:   Markdown 表格输出
- main:     主流程编排
"""

__version__ = "2.0.0"

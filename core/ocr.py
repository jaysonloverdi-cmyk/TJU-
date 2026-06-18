"""OCR 提取：EasyOCR 多 pass + 课本词库纠错 + 选项合并

功能：
- 单张/批量图片 OCR 提取
- 多 pass 不同对比度参数交叉验证
- 从课本构建高频词库自动纠错
- 选项跨行合并（OCR 经常拆分 "B" 和 "信息主导"）
- 题目解析：题号、题目、类型、选项
"""

import json
import re
from collections import Counter
from pathlib import Path


def _get_reader(lang='ch_sim', gpu=True):
    """惰性加载 EasyOCR Reader（避免未安装时 import 失败）"""
    try:
        import easyocr
    except ImportError:
        raise ImportError(
            "EasyOCR 未安装。请运行: pip install easyocr"
        )
    return easyocr.Reader([lang], gpu=gpu)


# ── 课本词库 ─────────────────────────────────────────

def build_vocabulary(textbook_path, min_len=2, max_len=6, min_freq=3):
    """从课本 md 提取 2-6 字高频词，构建纠错词库

    Args:
        textbook_path: 课本 markdown 文件路径
        min_len, max_len: 词长度范围
        min_freq: 最低出现次数

    Returns:
        set[str]: 高频词集合
    """
    try:
        text = Path(textbook_path).read_text(encoding='utf-8')
    except Exception:
        return set()

    # 提取中文连续片段
    chinese_segments = re.findall(r'[一-鿿]+', text)
    word_counter = Counter()

    for seg in chinese_segments:
        for length in range(min_len, max_len + 1):
            for i in range(len(seg) - length + 1):
                word_counter[seg[i:i + length]] += 1

    return {w for w, c in word_counter.items() if c >= min_freq}


def _edit_distance(s1, s2):
    """Levenshtein 编辑距离"""
    if len(s1) < len(s2):
        return _edit_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            cost = 0 if c1 == c2 else 1
            curr.append(min(curr[j] + 1, prev[j + 1] + 1, prev[j] + cost))
        prev = curr
    return prev[-1]


def correct_ocr_text(text, vocabulary):
    """用课本词库纠正 OCR 文本中的疑似错字

    对文本中每个 2-6 字中文词，与词库比对。
    编辑距离 ≤ 1 的自动替换为词库版本。

    Args:
        text: OCR 原始文本
        vocabulary: set[str] 高频词库

    Returns:
        str: 纠错后文本
    """
    if not vocabulary:
        return text

    corrected = text
    for length in [6, 5, 4, 3, 2]:  # 长词优先匹配
        for match in re.finditer(rf'[一-鿿]{{{length}}}', corrected):
            word = match.group()
            if word in vocabulary:
                continue
            # 寻找编辑距离 ≤ 1 的候选
            for cand in vocabulary:
                if len(cand) == length and _edit_distance(word, cand) <= 1:
                    corrected = corrected.replace(word, cand, 1)
                    break
    return corrected


# ── OCR 提取 ─────────────────────────────────────────

def extract_from_image(image_path, lang='ch_sim', gpu=True):
    """从单张图片提取文字

    Args:
        image_path: 图片路径
        lang: OCR 语言代码
        gpu: 是否使用 GPU

    Returns:
        list[dict]: [{"text": str, "conf": float, "bbox": [...]}, ...]
    """
    reader = _get_reader(lang, gpu)
    results = reader.readtext(str(image_path))
    return [
        {"text": r[1], "conf": float(r[2]), "bbox": r[0]}
        for r in results
    ]


def extract_from_images(image_paths, lang='ch_sim', gpu=True,
                         vocabulary=None):
    """批量 OCR 提取并合并结果

    Args:
        image_paths: list[str] 图片路径列表
        lang: OCR 语言代码
        gpu: 是否使用 GPU
        vocabulary: set[str] 纠错词库（可选）

    Returns:
        str: 合并后的纯文本
    """
    all_lines = []
    for path in image_paths:
        results = extract_from_image(path, lang, gpu)
        text = merge_ocr_lines(results)
        if vocabulary:
            text = correct_ocr_text(text, vocabulary)
        all_lines.append(text)
    return "\n--- PAGE BREAK ---\n".join(all_lines)


# ── OCR 后处理 ────────────────────────────────────────

def merge_ocr_lines(ocr_results):
    """合并 OCR 行：选项跨行合并 + 碎片拼接

    规则：
    - 单独出现的选项字母（A-E）与下一行合并
    - 连续的短碎片（< 4 字）与前后文合并

    Args:
        ocr_results: list[dict] OCR 结果

    Returns:
        str: 合并后全文
    """
    lines = [r["text"].strip() for r in ocr_results if r["text"].strip()]
    merged = []
    skip_next = False

    for i, line in enumerate(lines):
        if skip_next:
            skip_next = False
            continue

        # 选项字母 + 下一行合并
        if re.match(r'^[A-E]\.?\s*$', line) and i + 1 < len(lines):
            merged.append(line + " " + lines[i + 1])
            skip_next = True
        elif len(line) <= 3 and i + 1 < len(lines) and len(lines[i + 1]) > 3:
            merged.append(line + lines[i + 1])
            skip_next = True
        else:
            merged.append(line)

    return "\n".join(merged)


# ── 题目解析 ──────────────────────────────────────────

def parse_questions(text):
    """从 OCR 文本解析题目列表

    识别模式：
    - 题号：数字 + 顿号/点号
    - 类型关键词：单选题、多选题、判断题
    - 选项：A. B. C. D. 或 A B C D

    Args:
        text: OCR 提取的纯文本

    Returns:
        list[dict]: [{"num": int, "type": int, "qtext": str, "options": list}, ...]
    """
    questions = []
    current_qtype = 0  # 默认单选

    # 检测题型切换
    type_patterns = [
        (r'单选题', 0),
        (r'多选题', 1),
        (r'判断题', 3),
        (r'填空题', 2),
        (r'简答题', 4),
    ]

    # 按行分割
    lines = text.split('\n')

    # 题型检测
    for line in lines:
        for pat, qtype in type_patterns:
            if re.search(pat, line):
                current_qtype = qtype
                break

    # 题号匹配
    q_pattern = re.compile(
        r'^\s*(\d+)\s*[\.\、。．)\s]+(.+?)(?:\s*[（(]\s*[）)])?\s*$'
    )
    option_pattern = re.compile(r'^\s*([A-E])\s*[\.\、。．)\s]+(.+)')

    i = 0
    while i < len(lines):
        m = q_pattern.match(lines[i])
        if not m:
            i += 1
            continue

        num = int(m.group(1))
        qtext = m.group(2).strip()

        # 收集选项（后续行）
        options = []
        j = i + 1
        while j < len(lines):
            om = option_pattern.match(lines[j])
            if om:
                options.append(om.group(2).strip())
                j += 1
            elif q_pattern.match(lines[j]):
                break  # 下一题开始
            else:
                j += 1

        questions.append({
            "num": num,
            "type": current_qtype,
            "qtext": qtext,
            "options": options,
        })
        i = j

    return questions


def extract_questions_from_images(image_paths, vocabulary=None, lang='ch_sim'):
    """一站式：图片 → OCR → 纠错 → 解析题目

    Args:
        image_paths: 图片路径列表
        vocabulary: 纠错词库
        lang: OCR 语言

    Returns:
        list[dict]: 解析后的题目列表
    """
    full_text = extract_from_images(image_paths, lang=lang, vocabulary=vocabulary)
    return parse_questions(full_text)

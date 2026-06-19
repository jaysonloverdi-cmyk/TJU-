"""兜底搜索：课本关键词匹配 + 本地题库搜索

优先级：课本 > 本地题库

返回格式：
    {"answer": str, "source": "课本"|"本地题库", "detail": str, "confidence": float}
"""

import re
from pathlib import Path


def _is_garbled(text):
    """检测 OCR 垃圾文本（罕见 CJK 扩展区字符过多）"""
    if not text:
        return False
    rare = sum(1 for ch in text if '\U00020000' <= ch <= '\U0002FFFF'
               or '鸀' <= ch <= '꓏')
    return rare > 3 and rare / max(len(text), 1) > 0.2


# ── 课本搜索 ─────────────────────────────────────────

def search_textbook(question, textbook_path, context_lines=3):
    """在课本 md 中按关键词搜索

    策略：
    1. 提取题目中的 3-6 字关键词
    2. 在课本全文搜索关键词所在段落
    3. 返回包含该关键词的上下文

    Args:
        question: 题目文本
        textbook_path: 课本 md 路径
        context_lines: 返回的上下文行数

    Returns:
        dict | None: {"answer": str, "detail": str, "confidence": float}
    """
    try:
        text = Path(textbook_path).read_text(encoding='utf-8')
    except Exception:
        return None

    # 提取关键词：3-6 字中文词，按长度排序
    keywords = sorted(
        set(re.findall(r'[一-鿿]{3,6}', question)),
        key=len, reverse=True
    )[:5]

    if not keywords:
        return None

    lines = text.split('\n')
    # 过滤 OCR 垃圾行（罕见 CJK 扩展区字符密度 > 20%）
    clean_lines = [
        (i, l) for i, l in enumerate(lines)
        if not _is_garbled(l)
    ]
    best_match = None
    best_score = 0

    for keyword in keywords[:3]:
        for i, line in clean_lines:
            if keyword not in line:
                continue

            # 计算匹配分数：更多关键词匹配更高分
            context_start = max(0, i - context_lines)
            context_end = min(len(lines), i + context_lines + 1)
            context = '\n'.join(lines[context_start:context_end])
            # 跳过含垃圾字符的上下文
            if _is_garbled(context):
                continue

            score = sum(1 for kw in keywords if kw in context)
            if score > best_score:
                best_score = score
                best_match = {
                    "detail": context.strip(),
                    "line": i + 1,
                    "keyword": keyword,
                }

    if best_match and best_score >= 1:
        # 尝试从匹配段落中提取可能的答案关键词
        # 策略：找段落中被加粗的文本，或数字/专有名词
        bold_words = re.findall(r'\*\*(.+?)\*\*', best_match["detail"])
        answer_hint = bold_words[0] if bold_words else best_match["keyword"]

        return {
            "answer": answer_hint,
            "source": "课本",
            "detail": best_match["detail"],
            "keyword": best_match["keyword"],
            "line": best_match["line"],
            "confidence": 0.80,
        }

    return None


# ── 本地题库搜索 ─────────────────────────────────────

def search_local_bank(question, bank_path, max_lines_context=5):
    """在本地题库中模糊搜索

    支持多种题库格式：
    - 纯文本格式：题目 + 答案
    - JSON 格式：[{"q": ..., "a": ...}]
    - 结构化格式：题号 + 题干 + 选项 + 答案

    Args:
        question: 题目文本
        bank_path: 题库文件路径

    Returns:
        dict | None: {"answer": str, "detail": str, "confidence": float}
    """
    path = Path(bank_path)
    if not path.exists():
        return None

    try:
        content = path.read_text(encoding='utf-8')
    except Exception:
        return None

    # 尝试 JSON 格式
    try:
        import json
        bank = json.loads(content)
        if isinstance(bank, list):
            return _search_json_bank(question, bank)
    except (json.JSONDecodeError, ValueError):
        pass

    # 纯文本格式：按行搜索
    return _search_text_bank(question, content, max_lines_context)


def _search_json_bank(question, bank):
    """搜索 JSON 格式题库"""
    keywords = _extract_keywords(question, min_len=3)

    best_entry = None
    best_score = 0

    for entry in bank:
        qtext = entry.get("q", "") or entry.get("question", "")
        score = sum(1 for kw in keywords if kw in qtext)
        if score > best_score:
            best_score = score
            best_entry = entry

    if best_entry and best_score >= 1:
        answer = best_entry.get("a") or best_entry.get("answer") or best_entry.get("answer_string", "")
        if isinstance(answer, list):
            answer = ", ".join(answer)
        return {
            "answer": answer,
            "source": "本地题库",
            "detail": json.dumps(best_entry, ensure_ascii=False, indent=2),
            "confidence": 0.90,
        }

    return None


def _search_text_bank(question, content, context_lines):
    """搜索纯文本格式题库"""
    keywords = _extract_keywords(question, min_len=3)
    if not keywords:
        return None

    lines = content.split('\n')
    best_match = None
    best_score = 0

    # 搜索每个关键词
    for kw in keywords[:5]:
        for i, line in enumerate(lines):
            if kw not in line:
                continue
            # 扩大上下文
            ctx_start = max(0, i - context_lines)
            ctx_end = min(len(lines), i + context_lines + 1)
            context = '\n'.join(lines[ctx_start:ctx_end])

            score = sum(1 for k in keywords if k in context)
            if score > best_score:
                best_score = score
                best_match = {
                    "context": context,
                    "line": i + 1,
                    "matched_keyword": kw,
                }

    if best_match and best_score >= 1:
        ctx = best_match["context"]
        # 1. 提取 Markdown 加粗内容（wby题库.md 格式）
        bold = re.findall(r'\*\*(.+?)\*\*', ctx)
        # 2. 常见答案模式
        answer_patterns = [
            r'答案[是为：:]\s*(.+?)(?:\n|$)',
            r'正确答[案项][是为：:]\s*(.+?)(?:\n|$)',
            r'[（(]\s*([A-E]+)\s*[）)]',
        ]
        answer = None
        if bold:
            # 最长的加粗项最可能是答案
            answer = max(bold, key=len)
        if not answer:
            for pat in answer_patterns:
                m = re.search(pat, ctx, re.IGNORECASE)
                if m:
                    answer = m.group(1).strip()
                    break

        return {
            "answer": answer or "(见上下文)",
            "source": "本地题库",
            "detail": ctx.strip(),
            "line": best_match["line"],
            "keyword": best_match["matched_keyword"],
            "confidence": 0.88 if answer else 0.70,
        }

    return None


def _extract_keywords(text, min_len=3, max_len=8):
    """从文本中提取中文关键词"""
    keywords = []
    for length in range(max_len, min_len - 1, -1):
        keywords.extend(
            re.findall(rf'[一-鿿\w]{{{length},}}', text)
        )
    # 去重 + 按长度排序
    seen = set()
    unique = []
    for kw in sorted(keywords, key=len, reverse=True):
        if kw not in seen:
            seen.add(kw)
            unique.append(kw)
    return unique[:10]

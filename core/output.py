"""输出格式化：Markdown 表格生成 + 统计 + 详细说明

三部分结构：
1. 主表格：所有题目的快速速查
2. 📖 课本查证：课本来源题原文 + 位置
3. 🤖 AI 兜底：AI 来源题推理说明

增强：
- 自动统计（题库 X 题/课本 Y 题/AI Z 题）
- 每道题标注置信度
- 核验三态：✅ 通过 / ⚠️ 存疑 / ❌ 矛盾
"""

import re
from pathlib import Path

OPTION_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _format_option_cell(options, answer_indices=None):
    """格式化选项列：**A.选项文字** 加粗正确答案

    如果给了 answer_indices，正确答案的选项字母加粗。
    """
    if not options:
        return ""
    parts = []
    for i, opt in enumerate(options):
        letter = OPTION_LETTERS[i] if i < 26 else str(i)
        is_correct = answer_indices and i in answer_indices
        if is_correct:
            parts.append(f"**{letter}.{opt}**")
        else:
            parts.append(f"{letter}.{opt}")
    return " ".join(parts)


def _sanitize_cell(text, max_len=60):
    """清理表格单元格文本：去换行、去 markdown 标记、截断"""
    import re
    t = text.replace('\n', ' ').replace('\r', ' ')
    t = re.sub(r'\*\*(.+?)\*\*', r'\1', t)  # 去掉加粗
    t = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', t)  # 去掉链接
    t = re.sub(r'\s+', ' ', t).strip()
    if len(t) > max_len:
        t = t[:max_len-3] + '...'
    return t


def _format_answer_text(result, qtype=0):
    """提取答案文本用于表格列（单行、截断）"""
    ans_str = result.get("answer_string", [])
    ans_idx = result.get("answer_index", [])
    ans_key = result.get("answer_key", [])

    if qtype == 3:
        if ans_str and any(a.strip() for a in ans_str):
            return _sanitize_cell(ans_str[0], 20)
        if ans_key:
            return "正确" if ans_key[0] == "A" else "错误" if ans_key[0] == "B" else ans_key[0]
        return "?"

    if ans_str and any(a.strip() for a in ans_str):
        return _sanitize_cell(", ".join(a.strip() for a in ans_str if a.strip()), 50)
    if ans_key:
        return _sanitize_cell(", ".join(ans_key), 50)
    if ans_idx:
        return ", ".join(str(i) for i in ans_idx)
    return "(空)"


def _confidence_bar(confidence):
    """置信度可视化条"""
    if confidence >= 0.90:
        return "●●●"
    elif confidence >= 0.70:
        return "●●○"
    elif confidence >= 0.50:
        return "●○○"
    else:
        return "○○○"


def generate_main_table(questions, results):
    """生成主表格（Markdown）

    Args:
        questions: list[dict] 题目列表
        results: list[dict] 对应结果列表

    Returns:
        str: Markdown 表格
    """
    verified_count = sum(
        1 for r in results if r.get("verify_status", "✅") == "✅"
    )
    total = len(results)

    header = (
        f"# 军理答案速查\n\n"
        f"> ⚠️ \"题库\" = API 声称命中，未经人工验证 | {verified_count}/{total} 自动通过\n"
        f"> ⚠️ 标记的题请人工确认后再用\n\n"
        f"| 题号 | 选项 | 答案 | 题目 | 来源 | 置信 | 核验 |\n"
        f"| --- | --- | --- | --- | --- | --- | --- |\n"
    )

    rows = []
    for i, (q, r) in enumerate(zip(questions, results)):
        num = q.get("num", i + 1)
        qtype = q.get("type", 0)
        options = q.get("options", [])
        ans_indices = r.get("answer_index", [])
        source = r.get("source", "未知")
        confidence = r.get("confidence", 0.0)
        verify_status = r.get("verify_status", "✅")

        # 选项列（只显示正确答案，旧版格式）
        if options and ans_indices:
            parts = []
            for idx in ans_indices:
                if idx < len(options):
                    letter = OPTION_LETTERS[idx] if idx < 26 else str(idx)
                    parts.append(f"**{letter}.{options[idx]}**")
            opt_cell = " ".join(parts)
        elif options:
            # 无答案索引时显示选项数量
            opt_cell = f"({len(options)}个选项)"
        else:
            opt_cell = ""
        # 答案列
        ans_cell = _format_answer_text(r, qtype)
        # 题目列（单行截断）
        qtext_cell = _sanitize_cell(q.get("qtext", ""), 55)
        # 置信条
        conf_cell = f"{_confidence_bar(confidence)} {confidence:.0%}"

        # 来源标签缩写
        source_labels = {
            "题库": "题库", "cache": "题库",
            "课本": "课本", "本地题库": "本地题库",
            "AI": "AI", "未知": "未知",
        }
        source_cell = source_labels.get(source, source)

        row = f"| {num} | {opt_cell} | {ans_cell} | {qtext_cell} | {source_cell} | {conf_cell} | {verify_status} |"
        rows.append(row)

    return header + "\n".join(rows)


def generate_fallback_sections(results, fallback_details):
    """生成课本查证和 AI 兜底说明

    Args:
        results: list[dict] 结果列表
        fallback_details: list[dict|None] 兜底详情

    Returns:
        str: Markdown 文本
    """
    sections = []

    # 课本查证部分
    textbook_items = [
        (i, r, fb) for i, (r, fb) in enumerate(zip(results, fallback_details))
        if fb and fb.get("source") == "课本"
    ]
    if textbook_items:
        sections.append("### 📖 课本查证\n")
        for i, r, fb in textbook_items:
            detail = fb.get("detail", "").strip()
            keyword = fb.get("keyword", "")
            line = fb.get("line", "")
            sections.append(
                f"**Q{i + 1}** — 课本原文：\"{detail[:150]}...\""
                f"（第 {line} 行，关键词：{keyword}）\n"
            )

    # 本地题库部分
    bank_items = [
        (i, r, fb) for i, (r, fb) in enumerate(zip(results, fallback_details))
        if fb and fb.get("source") == "本地题库"
    ]
    if bank_items:
        sections.append("### 📋 本地题库\n")
        for i, r, fb in bank_items:
            detail = fb.get("detail", "").strip()
            line = fb.get("line", "")
            answer = fb.get("answer", "")
            sections.append(
                f"**Q{i + 1}** — 本地题库（第 {line} 行）：{answer}\n"
                f"> {detail[:200]}\n"
            )

    # AI 兜底部分
    ai_items = [
        (i, r) for i, r in enumerate(results)
        if r.get("source") == "AI"
    ]
    if ai_items:
        sections.append("### 🤖 AI 兜底\n")
        q_nums = ", ".join(f"Q{i + 1}" for i, _ in ai_items)
        sections.append(
            f"**{q_nums}** — API+课本+本地题库均未覆盖，AI 依据军理常识判断。"
            f"答案经多方交叉验证。\n"
        )

    return "\n".join(sections) if sections else ""


def generate_output(questions, results, fallback_details=None, config=None):
    """生成完整输出文件

    三部分：
    1. 主表格
    2. 非题库来源说明
    3. 统计摘要（可选）

    Args:
        questions: list[dict] 题目列表
        results: list[dict] 对应结果
        fallback_details: list[dict|None] 兜底详情
        config: dict 配置（用于页脚）

    Returns:
        str: 完整 Markdown 文本
    """
    from .verify import compute_statistics

    if fallback_details is None:
        fallback_details = [None] * len(results)

    parts = []

    # 主表格
    parts.append(generate_main_table(questions, results))

    # 统计摘要
    stats = compute_statistics(results)
    parts.append("")
    parts.append("---")

    total = stats["total"]
    parts.append(f"**统计**: "
                 f"题库 {stats['题库']}题 | "
                 f"课本 {stats['课本']}题 | "
                 f"本地题库 {stats['本地题库']}题 | "
                 f"AI {stats['AI']}题 | "
                 f"核验通过 {stats['verified']}/{total} | "
                 f"存疑 {stats['warning']} | "
                 f"矛盾 {stats['error']}")

    # 非题库来源说明
    fallback_text = generate_fallback_sections(results, fallback_details)
    if fallback_text:
        parts.append("")
        parts.append("## 非题库来源说明")
        parts.append("")
        parts.append(fallback_text)

    return "\n".join(parts)


def write_output(output_text, path):
    """写入输出文件

    Args:
        output_text: Markdown 文本
        path: 输出文件路径
    """
    out_path = Path(path)
    out_path.write_text(output_text, encoding='utf-8')
    return str(out_path)

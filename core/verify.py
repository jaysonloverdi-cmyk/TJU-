"""核验模块：跨来源校验 + 逻辑矛盾检测

核验状态：
- ✅ 通过：答案逻辑一致，无矛盾
- ⚠️ 存疑：多源答案不一致，或置信度偏低
- ❌ 矛盾：明显逻辑错误或来源标注错误
"""

import re


def verify_answer(question, result, fallback_result=None):
    """逐题核验

    Args:
        question: dict {"num": int, "type": int, "qtext": str, "options": list}
        result: dict API 搜索返回结果
        fallback_result: dict | None 课本/本地题库兜底结果

    Returns:
        dict: {"status": "✅"|"⚠️"|"❌", "reason": str}
    """
    status = "✅"
    reasons = []

    source = result.get("source", "未知")
    confidence = result.get("confidence", 0.0)
    ans_strings = result.get("answer_string", [])
    ans_indices = result.get("answer_index", [])

    # 1. 空答案检测
    if not ans_strings or not any(a.strip() for a in ans_strings):
        if not ans_indices:
            status = "❌"
            reasons.append("API 无有效答案")

    # 2. 来源一致性
    if source == "AI" and fallback_result:
        fb_ans = fallback_result.get("answer", "")
        api_ans = ans_strings[0] if ans_strings else ""
        # 简单对比：AI 答案与课本/题库不一致
        if fb_ans and api_ans and _answers_conflict(api_ans, fb_ans):
            status = "⚠️"
            reasons.append(
                f"AI 答案与{fallback_result.get('source', '兜底')}不一致"
            )

    # 3. 置信度偏低
    if confidence < 0.5:
        if status == "✅":
            status = "⚠️"
        reasons.append(f"置信度偏低 ({confidence:.0%})")

    # 4. 答案是否匹配选项（最重要的人工辅助信号）
    qtype = question.get("type", 0)
    options = question.get("options", [])
    if qtype in (0, 1) and options:
        ans_texts = [a.strip() for a in ans_strings if a.strip()]
        if ans_texts:
            opt_set = set(o.strip().strip('"\'""') for o in options)
            ans_set = set(ans_texts)
            matched = ans_set & opt_set
            if not matched:
                status = "⚠️"
                reasons.append(f"答案与选项不匹配: {', '.join(ans_texts[:2])}")
            elif len(ans_texts) > len(options):
                status = "⚠️"
                reasons.append("答案数量超过选项数")

    # 4.5 疑似匹错（search.py 标记了 _possible_mismatch）
    if result.get("_possible_mismatch"):
        status = "⚠️"
        reasons.append(result.get("_mismatch_detail", "题库疑似匹配到其他题"))

    # 5. 长答案检测（可能是解释而非直接答案）
    if ans_strings:
        first_ans = ans_strings[0].strip()
        if len(first_ans) > 50:
            if status == "✅":
                status = "⚠️"
            reasons.append(f"答案过长({len(first_ans)}字)，可能非直接答案")

    # 4. 判断题特殊检查
    qtype = question.get("type", 0)
    if qtype == 3:
        ans_str = ans_strings[0].strip() if ans_strings else ""
        if ans_str and ans_str not in ("对", "错", "正确", "错误", "A", "B"):
            if status == "✅":
                status = "⚠️"
            reasons.append(f"判断题答案格式异常: {ans_str}")

    # 5. 多选/单选选项匹配
    if qtype in (0, 1) and ans_indices:
        options = question.get("options", [])
        if options:
            max_idx = len(options) - 1
            for idx in ans_indices:
                if idx > max_idx:
                    status = "⚠️"
                    reasons.append(
                        f"答案索引 {idx} 超出选项范围 (0-{max_idx})"
                    )
                    break

    return {
        "status": status,
        "reason": "; ".join(reasons) if reasons else "核验通过",
    }


def _answers_conflict(a1, a2):
    """简单判断两个答案是否冲突"""
    a1n = a1.strip().lower()
    a2n = a2.strip().lower()

    # 完全相同不冲突
    if a1n == a2n:
        return False
    # 一个包含另一个不冲突
    if a1n in a2n or a2n in a1n:
        return False

    # 对错矛盾
    if ("对" in a1n or "正确" in a1n) and ("错" in a2n or "错误" in a2n):
        return True
    if ("错" in a1n or "错误" in a1n) and ("对" in a2n or "正确" in a2n):
        return True

    # 其他情况：不同即为潜在冲突
    return True


def verify_batch(questions, results):
    """批量核验所有题目

    Args:
        questions: list[dict] 题目列表
        results: list[dict] 搜索结果列表

    Returns:
        list[dict]: 带核验结果的题目列表
    """
    verified = []
    for q, r in zip(questions, results):
        v = verify_answer(q, r)
        r["verify_status"] = v["status"]
        r["verify_reason"] = v["reason"]
        verified.append({**q, "result": r})
    return verified


def compute_statistics(results):
    """计算来源统计

    Returns:
        dict: {"题库": int, "课本": int, "AI": int, "未知": int,
               "total": int, "verified": int, "warning": int, "error": int}
    """
    stats = {"题库": 0, "课本": 0, "本地题库": 0, "AI": 0, "未知": 0,
             "total": len(results), "verified": 0, "warning": 0, "error": 0}

    for r in results:
        source = r.get("source", "未知")
        # 归并到标准分类
        if source in ("题库", "cache"):
            stats["题库"] += 1
        elif source in ("课本",):
            stats["课本"] += 1
        elif source in ("本地题库",):
            stats["本地题库"] += 1
        elif source == "AI":
            stats["AI"] += 1
        else:
            stats["未知"] += 1

        vs = r.get("verify_status", "✅")
        if vs == "✅":
            stats["verified"] += 1
        elif vs == "⚠️":
            stats["warning"] += 1
        elif vs == "❌":
            stats["error"] += 1

    return stats

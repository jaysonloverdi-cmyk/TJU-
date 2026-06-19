#!/usr/bin/env python3
"""搜题工具：模糊搜索，优先命中题库。

重构说明 (v2.0):
    核心逻辑已迁移到 core/ 包中，本文件保留为向后兼容的 CLI 入口。
    新代码请使用: python -m core.main config.yaml

用法:
    # 单题 CLI（兼容旧版）
    python itihey_search.py "1+1=?" "2|3|4|5" 0

    # 批量流程（新版推荐）
    python -m core.main config.yaml 测试1 --questions questions.json
"""

import json
import os
import re
import sys
import time

import requests

# 直接导入核心模块（保持单文件也可独立运行）
# 如果 core/ 包可用，代理到 core.search；否则使用内置实现
try:
    from core.search import (
        normalize, rephrase_question, call_api,
        looks_like_bank_answer, fuzzy_search,
        format_answer, format_option_detail, determine_verdict,
        SearchCache,
    )
    _USE_CORE = True
except ImportError:
    _USE_CORE = False

# ── 模块级常量（core 和 fallback 共用）─────────────────
OPTION_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# ── 内置降级实现（core/ 不可用时使用）─────────────────

if not _USE_CORE:
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    API_URL = "https://platform.itihey.com/v1/search"
    PUNC_MAP = str.maketrans(
        "，。！？、；：""''　（）《》【】",
        ",.!?,;:""'' ()<>[]"
    )

    def normalize(text):
        variants = [text]
        t = text.translate(PUNC_MAP)
        if t != text: variants.append(t)
        t2 = re.sub(r"[^\w一-鿿]", "", text)
        if t2 != text: variants.append(t2)
        t3 = re.sub(r"[（(]\s*[）)]", "", text)
        if t3 != text: variants.append(t3)
        clean = re.sub(r"[^\w一-鿿]", "", text)
        for seg in sorted(re.findall(r"[一-鿿]{8,}", clean), key=len, reverse=True)[:2]:
            if seg not in variants: variants.append(seg)
        seen = set()
        result = []
        for v in variants:
            if v not in seen and len(v) >= 4:
                seen.add(v)
                result.append(v)
        return result

    def call_api(api_key, question, options=None, qtype=0):
        payload = {"question": question, "type": qtype}
        if options:
            payload["options"] = options
        try:
            resp = requests.post(API_URL, json=payload,
                                 headers={"Content-Type": "application/json", "x-api-key": api_key},
                                 timeout=30)
            return resp.json()
        except Exception as e:
            return {"_error": str(e)}

    def looks_like_bank_answer(ans_text):
        if not ans_text: return False
        patterns = [
            r'正确答案', r'正确选项', r'参考答案', r'答案为',
            r'[A-E][\s\.、。）]',
            r'解析', r'说明', r'\d+[\.\、]',
            r'故选', r'故本题', r'因此.*选',
        ]
        return any(re.search(p, ans_text) for p in patterns)

    def rephrase_question(question):
        variants = []
        q = question
        for prefix in ['以下哪条是', '以下哪个是', '以下哪项是', '以下哪一个选项', '下列关于', '关于']:
            if q.startswith(prefix):
                variants.append(q[len(prefix):])
        for suffix in ['说法不正确的是', '不属于的是', '不包括的是', '不正确是', '错误的是']:
            if q.endswith(suffix):
                variants.append(q[:-len(suffix)])
        variants.append(q.replace('?', '。').replace('？', '。'))
        return [v for v in variants if v != q]

    def fuzzy_search(api_key, question, options=None, qtype=0):
        variants = normalize(question) + rephrase_question(question)
        seen = set()
        unique = [v for v in variants if not (v in seen or seen.add(v))]
        best = None

        def try_result(result, variant=""):
            nonlocal best
            ans = result.get("answer_string", [])
            ans_text = ans[0] if ans else ''
            is_bank = result.get("use_ai") is False
            is_valid = bool(ans) and any(a.strip() for a in ans)
            looks_bank = looks_like_bank_answer(ans_text)
            if is_bank and is_valid:
                return result
            if looks_bank and ans_text:
                result['use_ai'] = False
                return result
            if best is None:
                best = result
            elif is_bank and best.get('use_ai', True):
                best = result
            return None

        for variant in unique:
            result = call_api(api_key, variant, options, qtype)
            if "_error" in result or "answer_string" not in result:
                continue
            found = try_result(result, variant)
            if found:
                return found
            time.sleep(0.10)

        if options and (best is None or best.get('use_ai', True)):
            result = call_api(api_key, question, None, qtype)
            if not result.get('_error') and result.get('answer_string'):
                found = try_result(result, question)
                if found:
                    return found

        return best

    def format_answer(result):
        ans_str = result.get("answer_string", [])
        ans_key = result.get("answer_key", [])
        ans_idx = result.get("answer_index", [])
        if ans_str and any(a.strip() for a in ans_str):
            return ", ".join(ans_str)
        if ans_key:
            return ", ".join(ans_key)
        if ans_idx:
            return ", ".join(str(i) for i in ans_idx)
        return "(空)"

    def format_option_detail(options, ans_idx):
        if not options or not ans_idx:
            return ""
        parts = []
        for idx in ans_idx:
            if idx < len(options):
                letter = OPTION_LETTERS[idx] if idx < 26 else str(idx)
                parts.append(f"{letter}. {options[idx]}")
        return " | ".join(parts)

    def determine_verdict(result):
        ans_str = result.get("answer_string", [])
        ak = result.get("answer_key", [])
        if ans_str and any(a.strip() for a in ans_str):
            return ans_str[0]
        if ak and ak[0] == "A":
            return "对"
        if ak and ak[0] == "B":
            return "错"
        return "?"


# ── 输出 (兼容旧版) ──────────────────────────────────

def show_part1(result, options=None, qtype=0):
    """第一部分：紧凑速查"""
    use_ai = result.get("use_ai", True)
    tag = "TK" if not use_ai else "AI"

    if qtype == 3:
        verdict = determine_verdict(result)
        print(f"  -> {verdict} [{tag}]")
        return

    ans = format_answer(result)
    detail = ""
    if options and result.get("answer_index"):
        detail = " " + format_option_detail(options, result["answer_index"])
    print(f"  -> {ans} [{tag}]{detail}")


def show_part2(question, result, options=None, qtype=0):
    """第二部分：题目 + 正确选项 + 命中标记"""
    use_ai = result.get("use_ai", True)
    tag = "题库命中" if not use_ai else "AI兜底"
    ans_str = result.get("answer_string", [])
    ans_idx = result.get("answer_index", [])

    print(f"  Q: {question}")

    if qtype == 3:
        verdict = determine_verdict(result)
        print(f"  {tag}", end="")
        if verdict and verdict != "?":
            print(f" | {verdict}")
        else:
            print()
    else:
        if options and ans_idx:
            parts = []
            for idx in ans_idx:
                if idx < len(options):
                    letter = OPTION_LETTERS[idx] if idx < 26 else str(idx)
                    parts.append(f"{letter}. {options[idx]}")
            print(f"  {', '.join(parts)} [{tag}]")
        elif ans_str and any(a.strip() for a in ans_str):
            print(f"  {', '.join(ans_str)} [{tag}]")
        else:
            print(f"  (无答案) [{tag}]")


# ── 主入口 ────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python itihey_search.py <题目> [选项以|分隔] [类型0-4]")
        print("      python itihey_search.py --batch config.yaml 测试N")
        print("类型: 0=单选 1=多选 2=填空 3=判断 4=简答")
        sys.exit(1)

    # 批量模式
    if sys.argv[1] == "--batch":
        config_path = sys.argv[2] if len(sys.argv) > 2 else "config.yaml"
        test_name = sys.argv[3] if len(sys.argv) > 3 else None
        from core.main import main
        main(config_path, test_name)
        sys.exit(0)

    # 单题模式（兼容旧版）
    api_key = os.environ.get("ITIHEY_KEY")
    if not api_key:
        print("请设置环境变量: set ITIHEY_KEY=itk_your_key")
        sys.exit(1)

    question = sys.argv[1]
    options = sys.argv[2].split("|") if len(sys.argv) > 2 else None
    qtype = int(sys.argv[3]) if len(sys.argv) > 3 else 0

    result = fuzzy_search(api_key, question, options, qtype)

    print("=" * 50)
    print("  [答案速查]")
    show_part1(result, options, qtype)
    print()
    print("  [题目详情]")
    show_part2(question, result, options, qtype)
    print("=" * 50)

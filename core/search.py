"""搜题核心：模糊搜索 + API 调用 + 缓存

缓存策略：
- 题库答案（use_ai=False）缓存 7 天
- AI 答案（use_ai=True）缓存 1 天
- SQLite 单文件存储，零配置
"""

import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path

import requests

API_URL = "https://platform.itihey.com/v1/search"
OPTION_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
PUNC_MAP = str.maketrans(
    "，。！？、；：""''　（）《》【】",
    ",.!?,;:""'' ()<>[]"
)

# 缓存 TTL（秒）
BANK_CACHE_TTL = 7 * 24 * 3600   # 题库答案 7 天
AI_CACHE_TTL = 1 * 24 * 3600     # AI 答案 1 天


# ── 缓存层 ───────────────────────────────────────────

class SearchCache:
    """SQLite 缓存：存 (question_hash, answer_json, source, timestamp)"""

    def __init__(self, db_path="cache.db"):
        self.db_path = str(db_path)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    hash TEXT PRIMARY KEY,
                    question TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    source TEXT NOT NULL,
                    timestamp REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON cache(timestamp)")

    def _hash(self, question, options=None, qtype=0):
        key = question + "|" + ("|".join(options) if options else "") + f"|type={qtype}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def get(self, question, options=None, qtype=0):
        h = self._hash(question, options, qtype)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT result_json, source, timestamp FROM cache WHERE hash=?",
                (h,)
            ).fetchone()
        if row is None:
            return None

        result_json, source, ts = row
        ttl = BANK_CACHE_TTL if source == "bank" else AI_CACHE_TTL
        if time.time() - ts > ttl:
            self._delete(h)
            return None

        return json.loads(result_json)

    def put(self, question, options, qtype, result):
        h = self._hash(question, options, qtype)
        is_bank = result.get("use_ai") is False
        source = "bank" if is_bank else "ai"
        result_json = json.dumps(result, ensure_ascii=False)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache(hash, question, result_json, source, timestamp) VALUES(?,?,?,?,?)",
                (h, question, result_json, source, time.time())
            )

    def _delete(self, h):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM cache WHERE hash=?", (h,))

    def stats(self):
        with sqlite3.connect(self.db_path) as conn:
            bank = conn.execute(
                "SELECT COUNT(*) FROM cache WHERE source='bank'"
            ).fetchone()[0]
            ai = conn.execute(
                "SELECT COUNT(*) FROM cache WHERE source='ai'"
            ).fetchone()[0]
        return {"bank": bank, "ai": ai}


# ── 文本处理 ─────────────────────────────────────────

def normalize(text):
    """生成题目的多种模糊变体：标点替换 / 去非中文 / 去空括号 / 长片段
    搜索优先级：去括号版 > 原文 > 其他变体（避免全角括号匹错题）
    """
    variants = []
    # 0. 去空括号版优先（全角/半角括号问题：（）可能匹到不同题）
    t0 = re.sub(r"[（(]\s*[）)]", "", text)
    if t0 != text and len(t0) >= 4:
        variants.append(t0)
    # 1. 原文
    variants.append(text)
    t = text.translate(PUNC_MAP)
    if t != text:
        variants.append(t)
    # 去下划线/填空题占位符 (___、__、____ 等)
    t2 = re.sub(r'_+', '', text)
    if t2 != text and len(t2) >= 4:
        variants.append(t2)
    t3 = re.sub(r"[^\w一-鿿]", "", text)
    if t3 != text:
        variants.append(t3)
    # 再去一次下划线（\w 包含 _）
    t3b = re.sub(r'_+', '', t3)
    if t3b != t3 and len(t3b) >= 4:
        variants.append(t3b)
    # 如果 t0 已经去括号了，t4 是重复的，跳过
    if t0 == text:  # 原文没有括号才试这个
        t4 = re.sub(r"[（(]\s*[）)]", "", text)
        if t4 != text:
            variants.append(t4)
    clean = re.sub(r"[^\w一-鿿]", "", text)
    clean = re.sub(r'_+', '', clean)
    for seg in sorted(re.findall(r"[一-鿿]{8,}", clean), key=len, reverse=True)[:2]:
        if seg not in variants:
            variants.append(seg)
    seen = set()
    result = []
    for v in variants:
        if v not in seen and len(v) >= 4:
            seen.add(v)
            result.append(v)
    return result


def rephrase_question(question):
    """生成题目的替换措辞（去前缀/去后缀/标点替换/关键词提取）"""
    variants = []
    q = question
    # 去前缀
    for prefix in [
        '以下哪条是', '以下哪个是', '以下哪项是', '以下哪一个选项',
        '下列关于', '关于', '下列哪条是', '下列哪个是', '下列哪项是',
        '以下哪一个不属于', '以下哪条不属于', '以下哪个不属于',
        '以下不属于', '哪一个不属于',
    ]:
        if q.startswith(prefix):
            variants.append(q[len(prefix):])
    # 去后缀
    for suffix in [
        '说法不正确的是', '不属于的是', '不包括的是',
        '不正确是', '错误的是', '不正确的是',
        '不属于', '不包括',
    ]:
        if q.endswith(suffix):
            variants.append(q[:-len(suffix)])
    variants.append(q.replace('?', '。').replace('？', '。'))
    # 提取核心关键词（8-16 字长片段，用于短词搜索）
    clean = re.sub(r'[^\w一-鿿]', '', q)
    for seg in sorted(re.findall(r'[一-鿿]{8,16}', clean), key=len, reverse=True)[:2]:
        if seg not in variants:
            variants.append(seg)
    return [v for v in variants if v != q]


def looks_like_bank_answer(ans_text):
    """检测答案文本是否包含题库结构标记"""
    if not ans_text:
        return False
    patterns = [
        r'正确答案', r'正确选项', r'参考答案', r'答案为',
        r'[A-E][\s\.、。）]',
        r'解析', r'说明', r'\d+[\.\、]',
        r'故选', r'故本题', r'因此.*选',
    ]
    return any(re.search(p, ans_text) for p in patterns)


def clean_answer(ans_text):
    """清洗 API 返回的答案：去"答案："前缀、截掉"解析："后半

    例：'答案：全域慑战\n解析：建设强大的现代化火箭军...'
      → '全域慑战'
    """
    if not ans_text:
        return ans_text
    t = ans_text.strip()
    # 去前缀
    for prefix in ['参考答案：', '正确答案：', '正确选项：', '答案为：', '答案为:', '答案：', '答案:',
                   '以下是对该题的分析及答案：', '解题思路：', '【解析】', '【答案】']:
        if t.startswith(prefix):
            t = t[len(prefix):].strip()
    # 取第一段（截掉解析/说明/注）
    for sep in ['\n解析', '\n说明', '\n注：', '\n注:', '\n（注', '\n(注', '\n解题思路']:
        idx = t.find(sep)
        if idx > 0:
            t = t[:idx].strip()
    # 去首尾标点
    t = t.strip('，。、；：""''')
    return t


def looks_like_structured_answer(ans_text):
    """更强判定：答案文本是否像题库返回的结构化答案

    当 API 标了 use_ai=true 但实际返回了带"答案："的结构化文本时，
    大概率是题库命中了但 API 标记有误。
    """
    if not ans_text:
        return False
    markers = [
        r'^答案[：:为]',           # 答案：XXX
        r'^正确[答案选项][：:为]',  # 正确答案：XXX
        r'^参考[答案][：:为]',     # 参考答案：XXX
        r'\n解析[：:]',            # 带解析段
        r'\n故选',                 # 故选
    ]
    return any(re.search(p, ans_text) for p in markers)


# ── API 调用（含重试）─────────────────────────────────

def call_api(api_key, question, options=None, qtype=0,
             retry_max=3, retry_delay_base=1.0, logger=None):
    """调用 itihey API，失败自动重试（指数退避）"""
    payload = {"question": question, "type": qtype}
    if options:
        payload["options"] = options

    last_error = None
    for attempt in range(1, retry_max + 1):
        try:
            resp = requests.post(
                API_URL, json=payload,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": api_key,
                },
                timeout=30
            )
            return resp.json()
        except requests.Timeout as e:
            last_error = f"timeout: {e}"
        except requests.ConnectionError as e:
            last_error = f"connection: {e}"
        except Exception as e:
            last_error = str(e)

        if attempt < retry_max:
            delay = retry_delay_base * (2 ** (attempt - 1))
            if logger:
                logger.warning(
                    "API 调用失败 (attempt %d/%d): %s — %ds 后重试",
                    attempt, retry_max, last_error, delay
                )
            time.sleep(delay)

    if logger:
        logger.error("API 调用最终失败: %s", last_error)
    return {"_error": last_error}


# ── 模糊搜索（整合缓存）──────────────────────────────

def fuzzy_search(api_key, question, options=None, qtype=0,
                 cache=None, logger=None):
    """核心搜题：缓存命中 → API 多轮模糊搜索 → 返回最佳结果

    返回 dict:
        answer_string, answer_key, answer_index, use_ai, source, confidence
    """
    # 1. 查缓存
    if cache:
        cached = cache.get(question, options, qtype)
        if cached:
            if logger:
                logger.info("缓存命中: %s...", question[:30])
            cached["source"] = "cache"
            return cached

    # 2. 构建搜索变体
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
        looks_structured = looks_like_structured_answer(ans_text)

        # 清洗答案：去"答案："前缀、截"解析："后半
        if ans_text:
            cleaned = clean_answer(ans_text)
            if cleaned != ans_text:
                result["answer_string"] = [cleaned]

        if is_bank and is_valid:
            result["confidence"] = 0.95
            result["source"] = "题库"
            return result
        if looks_bank and ans_text:
            result["use_ai"] = False
            result["confidence"] = 0.85
            result["source"] = "题库"
            result["_bank_matched"] = True
            return result
        # 即使 API 标了 AI，如果答案带结构化标记且匹配选项 → 标题库
        if looks_structured and ans_text:
            cleaned = ans_text
            result["use_ai"] = False
            result["_bank_matched"] = True
            # 检查是否匹错题
            if options and len(cleaned) > 10:
                opt_match = any(o.strip() in cleaned or cleaned in o.strip() for o in options)
                if not opt_match:
                    result["_possible_mismatch"] = True
                    result["_mismatch_detail"] = f"题库疑似匹配到其他题：{cleaned[:60]}"
                    result["source"] = "匹错"
                    result["confidence"] = 0.70
                    return result
            result["source"] = "题库"
            result["confidence"] = 0.85
            return result
        # 答案匹配选项：提置信度但不提前退出（让后续变体有机会命中更强信号）
        if options and is_valid:
            ans_set = set(a.strip() for a in ans if a.strip())
            opt_set = set(o.strip() for o in options)
            matched = ans_set & opt_set
            if len(matched) >= 2:
                result["confidence"] = max(result.get("confidence", 0), 0.80)
                result["_option_matched"] = True
            elif matched:
                result["confidence"] = max(result.get("confidence", 0), 0.70)
                result["_option_matched"] = True

        if best is None:
            best = result
        elif is_bank and best.get("use_ai", True):
            best = result
        return None

    # 3. 第一轮：带选项搜索
    for variant in unique:
        result = call_api(api_key, variant, options, qtype, logger=logger)
        if "_error" in result or "answer_string" not in result:
            continue
        found = try_result(result, variant)
        if found:
            best = found
            break
        time.sleep(0.10)

    # 4. 第二轮：不带选项搜索
    if best is None or (best.get("use_ai", True) and options):
        result = call_api(api_key, question, None, qtype, logger=logger)
        if not result.get("_error") and result.get("answer_string"):
            found = try_result(result, question)
            if found:
                best = found

    # 4.5 第三轮：用选项关键词反搜
    if best is None or best.get("use_ai", True):
        if options and len(options) >= 2:
            longest_opt = max(options, key=len)
            if len(longest_opt) >= 4:
                result = call_api(api_key, longest_opt, None, qtype, logger=logger)
                if not result.get("_error") and result.get("answer_string"):
                    found = try_result(result, longest_opt)
                    if found:
                        best = found

    # 5. 后处理
    if best is None:
        best = {"answer_string": [], "use_ai": True, "source": "未知", "confidence": 0.0}
    else:
        if "source" not in best:
            if best.get("use_ai") is False:
                best["source"] = "题库"
                best["confidence"] = max(best.get("confidence", 0), 0.90)
            else:
                best["source"] = "AI"
                best["confidence"] = max(best.get("confidence", 0), 0.60)
        if "confidence" not in best:
            best["confidence"] = 0.60 if best.get("use_ai", True) else 0.90

    # 6. 写缓存
    if cache and best.get("source") != "未知":
        cache.put(question, options, qtype, best)

    return best


# ── 答案提取工具 ──────────────────────────────────────

def format_answer(result):
    """从 API 结果提取可读答案"""
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
    """答案对应的选项详情"""
    if not options or not ans_idx:
        return ""
    parts = []
    for idx in ans_idx:
        if idx < len(options):
            letter = OPTION_LETTERS[idx] if idx < 26 else str(idx)
            parts.append(f"{letter}. {options[idx]}")
    return " | ".join(parts)


def determine_verdict(result):
    """判断题：从 API 结果推断对/错"""
    ans_str = result.get("answer_string", [])
    ak = result.get("answer_key", [])
    if ans_str and any(a.strip() for a in ans_str):
        return ans_str[0]
    if ak and ak[0] == "A":
        return "对"
    if ak and ak[0] == "B":
        return "错"
    return "?"

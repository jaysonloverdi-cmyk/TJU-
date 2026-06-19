"""主流程编排：串联 OCR → API 搜索 → 兜底 → 核验 → 输出

用法:
    python -m core.main <config.yaml> [test_name]

环境变量:
    ITIHEY_KEY: API 密钥（config.yaml 未设置时使用）
"""

import logging
import shutil
import sys
import time
from pathlib import Path

# 支持从任意目录运行
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR.parent))

from core.search import fuzzy_search, SearchCache
from core.fallback import search_textbook, search_local_bank
from core.verify import verify_answer, compute_statistics
from core.output import write_output, generate_output

# 可选依赖
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


# ── 配置加载 ─────────────────────────────────────────

def load_config(config_path):
    """加载 YAML/JSON 配置文件

    支持 YAML（需 PyYAML）和 JSON。
    环境变量覆盖：ITIHEY_KEY 优先于配置文件中的 api_key。
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    text = path.read_text(encoding='utf-8')

    # 尝试 YAML
    if HAS_YAML:
        try:
            config = yaml.safe_load(text)
            if isinstance(config, dict):
                return _resolve_config(config, path.parent)
        except yaml.YAMLError:
            pass

    # 尝试 JSON
    import json
    try:
        config = json.loads(text)
        if isinstance(config, dict):
            return _resolve_config(config, path.parent)
    except json.JSONDecodeError:
        pass

    raise ValueError(f"无法解析配置文件: {config_path}")


def _resolve_config(config, base_dir):
    """解析配置中的路径（相对于配置文件目录）"""
    # API Key: 环境变量优先
    import os
    if not config.get("api_key"):
        config["api_key"] = os.environ.get("ITIHEY_KEY", "")

    # 路径字段：如果是相对路径，相对于配置文件目录
    for key in ("textbook", "local_bank", "cache_db", "output_dir",
                "temp_dir", "log_file"):
        val = config.get(key)
        if val and not Path(val).is_absolute():
            config[key] = str(base_dir / val)

    # 默认值
    config.setdefault("ocr_lang", "ch_sim")
    config.setdefault("retry_max", 3)
    config.setdefault("retry_delay_base", 1.0)
    config.setdefault("cache_db", str(base_dir / "cache.db"))
    config.setdefault("temp_dir", str(base_dir / "temp"))
    config.setdefault("output_dir", str(base_dir))
    config.setdefault("log_file", str(base_dir / "error.log"))
    config.setdefault("keep_recent", 3)
    config.setdefault("output_format", "markdown")

    return config


# ── 日志设置 ─────────────────────────────────────────

def setup_logging(log_file=None, level=logging.INFO):
    """配置日志：控制台 + 文件"""
    logger = logging.getLogger("search_cli")
    logger.setLevel(level)

    # 控制台 handler
    if not logger.handlers:
        ch = logging.StreamHandler()
        ch.setLevel(logging.WARNING)  # 控制台只显示警告和错误
        ch.setFormatter(logging.Formatter(
            "[%(levelname)s] %(message)s"
        ))
        logger.addHandler(ch)

        if log_file:
            fh = logging.FileHandler(log_file, encoding='utf-8')
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            ))
            logger.addHandler(fh)

    return logger


# ── 临时文件管理 ─────────────────────────────────────

def cleanup_temp(temp_dir, keep_recent=3):
    """清理临时文件，保留最近 N 个

    Args:
        temp_dir: 临时文件目录
        keep_recent: 保留最近 N 个文件
    """
    path = Path(temp_dir)
    if not path.exists():
        return

    files = sorted(path.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)
    for f in files[keep_recent:]:
        try:
            if f.is_file():
                f.unlink()
            elif f.is_dir():
                shutil.rmtree(f)
        except OSError:
            pass


# ── OCR → 结构化文件 ─────────────────────────────────

def ocr_and_save(image_paths, output_json, config, save_raw=True):
    """图片 OCR → 解析题目 → 保存结构化 JSON

    这是"图片 → 可用数据"的关键一步。
    保存后 JSON 可反复使用，无需重新 OCR。

    Args:
        image_paths:   图片路径列表
        output_json:   输出 JSON 路径
        config:        配置 dict
        save_raw:      是否同步保存 OCR 原始文本

    Returns:
        list[dict]: 解析后的题目列表
    """
    import json

    vocabulary = None
    textbook_path = config.get("textbook", "")
    if textbook_path and Path(textbook_path).exists():
        from core.ocr import build_vocabulary
        print("构建课本词库...")
        vocabulary = build_vocabulary(textbook_path)

    from core.ocr import extract_questions_from_images

    questions = extract_questions_from_images(
        image_paths,
        vocabulary=vocabulary,
        lang=config.get("ocr_lang", "ch_sim")
    )

    # 保存结构化题目
    out_path = Path(output_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(questions, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )
    print(f"题目已保存: {out_path} ({len(questions)} 题)")

    return questions


def load_questions(source, config=None):
    """加载题目：支持多种来源

    source 可以是:
        - JSON 文件路径: "_new_qs.json"
        - 图片目录路径:  "测试5/"  → OCR + 自动保存 _parsed_测试5.json
        - 图片文件路径:  "测试5/img.jpg"
        - 图片文件列表:  ["img1.jpg", "img2.jpg"]
        - glob 通配符:   "测试5/*.jpg"

    自动检测类型并处理。OCR 结果自动保存为结构化 JSON。
    """
    import glob as glob_mod, json

    # 列表：多张图片
    if isinstance(source, list):
        images = [str(p) for p in source if Path(p).is_file()]
        if images:
            test_name = Path(images[0]).parent.name
            out = Path(images[0]).parent / f"_parsed_{test_name}.json"
            return ocr_and_save(images, str(out), config or {})
        raise FileNotFoundError(f"没有有效图片: {source}")

    path = Path(source)

    # 目录：扫描图片
    if path.is_dir():
        exts = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp'}
        images = sorted(
            str(p) for p in path.iterdir()
            if p.suffix.lower() in exts
        )
        if images:
            out = path / f"_parsed_{path.name}.json"
            return ocr_and_save(images, str(out), config or {})
        raise FileNotFoundError(f"目录 {source} 中没有图片")

    # glob 通配符
    if '*' in str(source):
        images = sorted(glob_mod.glob(str(source)))
        if images:
            test_name = Path(images[0]).parent.name
            out = Path(images[0]).parent / f"_parsed_{test_name}.json"
            return ocr_and_save(images, str(out), config or {})
        raise FileNotFoundError(f"通配符无匹配: {source}")

    # 单张图片文件
    ext = path.suffix.lower()
    if ext in ('.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp'):
        test_name = path.parent.name
        out = path.parent / f"_parsed_{test_name}.json"
        return ocr_and_save([str(path)], str(out), config or {})

    # JSON 文件
    if not path.exists():
        raise FileNotFoundError(f"题目文件不存在: {source}")

    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, list):
        raise ValueError(f"题目文件格式错误: 期望 JSON 数组")
    if not data:
        return []

    # 检测格式并转换
    first = data[0]
    if "qtext" in first or "num" in first:
        return data  # 已是 parsed 格式
    elif "q" in first:
        # 旧版 new_qs 格式 → 转换
        return [
            {
                "num": i + 1,
                "type": item.get("type", 0),
                "qtext": item.get("q", ""),
                "options": item.get("opts", "").split("|") if item.get("opts") else [],
            }
            for i, item in enumerate(data)
        ]
    else:
        raise ValueError(f"无法识别的题目格式，字段: {list(first.keys())}")


# ── 主流程 ────────────────────────────────────────────

def process_questions(questions, config, logger=None):
    """批量处理题目：搜索 → 兜底 → 核验

    Args:
        questions: list[dict] 题目列表
        config: dict 配置
        logger: logging.Logger

    Returns:
        tuple[list[dict], list[dict|None]]: (results, fallback_details)
    """
    api_key = config.get("api_key", "")
    if not api_key:
        raise ValueError("未设置 API Key。请在 config.yaml 中设置 api_key"
                         " 或设置环境变量 ITIHEY_KEY")

    textbook_path = config.get("textbook", "")
    bank_path = config.get("local_bank", "")
    cache_db = config.get("cache_db", "cache.db")

    cache = SearchCache(cache_db)
    if logger:
        logger.info("缓存状态: %s", cache.stats())

    results = []
    fallback_details = []

    for i, q in enumerate(questions):
        qnum = q.get("num", i + 1)
        qtext = q.get("qtext", "")
        qtype = q.get("type", 0)
        options = q.get("options", [])

        if logger:
            logger.info("处理 Q%d: %s...", qnum, qtext[:40])

        try:
            # 1. API 搜索（含缓存）
            result = fuzzy_search(
                api_key, qtext, options, qtype, cache=cache, logger=logger
            )

            # 2. 兜底：本地题库 > 课本（优先级）
            # 但如果 AI 答案已经匹配选项，跳过兜底（避免课本劣化答案）
            fb = None
            ans_match_opts = (
                result.get("_option_matched")
                or result.get("confidence", 0) >= 0.70
            )
            if result.get("source") in ("AI", "未知") and not ans_match_opts:
                # 本地题库优先
                if bank_path:
                    if logger:
                        logger.info("Q%d API 未命中，尝试本地题库...", qnum)
                    fb = search_local_bank(qtext, bank_path)

                # 课本搜索
                if not fb and textbook_path:
                    if logger:
                        logger.info("Q%d 本地题库无结果，尝试课本...", qnum)
                    fb = search_textbook(qtext, textbook_path)

                if fb:
                    result["source"] = fb["source"]
                    result["confidence"] = fb["confidence"]
                    result["answer_string"] = [fb.get("answer", "(见上下文)")]

            # 3. 核验
            verification = verify_answer(q, result, fb)
            result["verify_status"] = verification["status"]
            result["verify_reason"] = verification["reason"]

            results.append(result)
            fallback_details.append(fb)

        except Exception as e:
            if logger:
                logger.error("Q%d 处理失败: %s", qnum, e)
            error_result = {
                "answer_string": [f"错误: {e}"],
                "use_ai": True,
                "source": "错误",
                "confidence": 0.0,
                "verify_status": "❌",
                "verify_reason": str(e),
            }
            results.append(error_result)
            fallback_details.append(None)

        # 请求间隔
        if i < len(questions) - 1:
            time.sleep(0.15)

    return results, fallback_details


def main(config_path=None, test_name=None):
    """主入口：完整搜题流程

    Args:
        config_path: config.yaml 路径
        test_name: 测试名称（用于输出文件名）
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="军理搜题工作流 — 批量搜题 + 答案生成"
    )
    parser.add_argument(
        "config", nargs="?", default="config.yaml",
        help="配置文件路径 (默认: config.yaml)"
    )
    parser.add_argument(
        "test", nargs="?", default=None,
        help="测试名称（如 '测试1'）"
    )
    parser.add_argument(
        "--questions", "-q",
        help="JSON 题目文件路径（替代 OCR）"
    )
    parser.add_argument(
        "--images", "-i", nargs="+",
        help="试卷图片路径（自动 OCR）"
    )
    parser.add_argument(
        "--no-cleanup", action="store_true",
        help="不清理临时文件"
    )
    args = parser.parse_args()

    config_path = args.config
    test_name = args.test

    # 加载配置
    print(f"加载配置: {config_path}")
    config = load_config(config_path)

    # 设置日志
    logger = setup_logging(config.get("log_file"))
    logger.info("搜题工作流启动")

    # 加载题目（自动检测类型：JSON / 图片目录 / 图片列表 / 单张图片）
    if args.images:
        # --images 总是 list（nargs="+"），直接传给 load_questions
        questions = load_questions(args.images, config)
    elif args.questions:
        questions = load_questions(args.questions, config)
    else:
        print("请通过 --questions 或 --images 指定题目来源")
        print("  --questions data.json      从 JSON 加载")
        print("  --images 测试5/             从图片目录 OCR + 自动保存 _parsed_测试5.json")
        print("  --images img1.jpg img2.jpg  多张图片 OCR")
        sys.exit(1)

    print(f"共 {len(questions)} 道题")

    # 处理
    results, fallback_details = process_questions(questions, config, logger)

    # 输出
    output_text = generate_output(questions, results, fallback_details, config)
    output_dir = config.get("output_dir", ".")
    output_name = f"{test_name or 'output'} 答案.md"
    output_path = Path(output_dir) / output_name
    write_output(output_text, str(output_path))
    print(f"输出文件: {output_path}")

    # 统计
    stats = compute_statistics(results)
    print(f"统计: 题库 {stats['题库']} | 课本 {stats['课本']} | "
          f"本地题库 {stats['本地题库']} | AI {stats['AI']} | "
          f"核验 ▸ ✅{stats['verified']} ⚠️{stats['warning']} ❌{stats['error']}")

    # 清理临时文件
    if not args.no_cleanup:
        temp_dir = config.get("temp_dir", "temp")
        keep = config.get("keep_recent", 3)
        cleanup_temp(temp_dir, keep)
        logger.info("临时文件清理完成 (保留最近 %d 个)", keep)

    return results


if __name__ == "__main__":
    main()

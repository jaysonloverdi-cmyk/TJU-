"""交叉验证模块：融合 pipeline + 本地题库 + 多 AI → 终版答案

输入：
    - pipeline 输出的 答案.md
    - DeepSeek.md / Gemini.md / 豆包.md（AI 答案）
    - wby题库.md（本地题库）
    - 题目校对.md（题干）

输出：
    终版答案.md — 速查表 + 逐题说明

判定逻辑：
    🟢 题库 + AI 全票 → 直接采用，置信 0.98
    🟡 题库 vs AI 分歧 → 信题库，置信 0.90
    🟡 3AI 一致，无题库 → 采用 AI 共识，置信 0.80
    🔴 AI 内部分歧，无题库 → 标记人工确认
"""

import re
import json
from pathlib import Path
from collections import Counter


def parse_md_answers(filepath):
    """从 Markdown 提取答案。支持四种格式：
    1. [x] 复选框格式
    2. | Q1 | **E** | 表格格式
    3. | Q1 | E | 简化表格
    4. - **Q1 | 单选：E** Gemini 格式
    """
    text = Path(filepath).read_text(encoding='utf-8')
    answers = {}
    current_q = None

    for line in text.split('\n'):
        # [x] 格式
        m = re.match(r'### Q(\d+)', line)
        if m:
            current_q = int(m.group(1))
            answers[current_q] = []
            continue
        if current_q:
            opt = re.search(r'\[x\]\s*([A-Da-d])', line)
            if opt:
                answers[current_q].append(opt.group(1).upper())
            if re.search(r'\[x\]\s*对', line):
                answers[current_q] = ['对']
            if re.search(r'\[x\]\s*错', line):
                answers[current_q] = ['错']

        # 表格格式: | Q1 | **E**（...）|  or  | 1 | E |
        # 但跳过 pipeline 输出（列数 >= 6）
        if line.count('|') < 6:
            tm = re.match(r'\|\s*Q?(\d+)\s*\|\s*\*?\*?([A-E]+|对|错|正确|错误)\*?\*?', line)
            if tm:
                q = int(tm.group(1))
                ans = tm.group(2).replace('正确','对').replace('错误','错')
                if q not in answers:
                    answers[q] = [ans]

        # Gemini 格式: - **Q1 | 单选：E** or - **Q21 | 多选：A, B, C**
        gm = re.match(r'-\s*\*\*Q(\d+)\s*\|\s*(?:单选|多选|判断)\s*[：:]\s*([A-E对错,\s]+)\*\*', line)
        if gm:
            q = int(gm.group(1))
            ans_str = gm.group(2).replace('正确','对').replace('错误','错').replace(' ','').replace(',','')
            ans = list(ans_str) if len(ans_str) > 1 else [ans_str]
            if q not in answers:
                answers[q] = ans

        # 多选题: | Q21 | ABCDE |  or  Q21 | **A**、**B**、**C** |
        mm = re.match(r'\|\s*Q?(\d+)\s*\|\s*\*?\*?([A-E]{2,5})\*?\*?', line)
        if mm and not tm:  # Don't double-match single-letter answers
            q = int(mm.group(1))
            ans = list(mm.group(2))
            if q not in answers:
                answers[q] = ans
        # 豆包 format: Q21 | ABCDE (no leading pipe) or | Q1 | E | ... |
        dm = re.match(r'Q(\d+)\s*\|\s*([A-E]{1,5})\b', line)
        if dm:
            q = int(dm.group(1))
            ans = list(dm.group(2)) if len(dm.group(2)) > 1 else [dm.group(2)]
            if q not in answers:
                answers[q] = ans
        # 豆包 inline: | Q1 | E | 描述 |
        dm2 = re.match(r'\|\s*Q(\d+)\s*\|\s*([A-E]+|对|错)\s*\|', line)
        if dm2:
            q = int(dm2.group(1))
            ans = dm2.group(2).replace('正确','对').replace('错误','错')
            if q not in answers:
                answers[q] = [ans] if len(ans) == 1 else list(ans)

    # 豆包 single-line format: all content on one line
    if len(answers) < 10:  # Probably single-line format
        # Split by | |  patterns
        for part in re.split(r'\|\s*\|', text):
            dm = re.search(r'Q(\d+)\s*\|\s*([A-E对错]+)', part)
            if dm:
                q = int(dm.group(1))
                ans = dm.group(2).replace('正确','对').replace('错误','错')
                if q not in answers:
                    answers[q] = [ans] if len(ans) == 1 else list(ans)

    # 管道表格格式
    for line in text.split('\n'):
        if '|' in line and line[0] == '|' and '---' not in line and '题号' not in line:
            parts = [p.strip() for p in line.split('|')]
            if len(parts) < 6:
                continue
            try:
                q = int(parts[1])
            except ValueError:
                continue
            if q in answers:
                continue
            ans = parts[3]
            src = parts[5]
            ver = parts[7] if len(parts) > 7 else ''
            answers[q] = {'text': ans, 'source': src, 'verify': ver}

    # Re-read for pipeline table format properly
    if not answers or all(isinstance(v, list) and len(v) == 0 for v in answers.values()):
        text2 = Path(filepath).read_text(encoding='utf-8')
        for line in text2.split('\n'):
            if '|' in line and line[0] == '|' and '---' not in line and '题号' not in line and '答案' not in line:
                parts = [p.strip() for p in line.split('|')]
                if len(parts) < 6:
                    continue
                try:
                    q = int(parts[1])
                except ValueError:
                    continue
                if q in answers and isinstance(answers[q], dict):
                    continue
                ans = parts[3]
                src = parts[5] if len(parts) > 5 else ''
                ver = parts[7] if len(parts) > 7 else ''
                answers[q] = {'text': ans, 'source': src, 'verify': ver}

    return answers


def fmt(ans):
    """格式化答案为可比较字符串"""
    if isinstance(ans, list):
        return ''.join(sorted(ans))
    if isinstance(ans, dict):
        return ans.get('text', '-')
    return str(ans)


def load_options(test_path):
    """从题目校对.md 加载每题选项，用于文字↔字母互转"""
    vault_root = Path('D:/Documents/25262')
    candidates = [
        test_path / '题目校对.md',
        vault_root / f'{test_path.name} 题目校对.md',
        test_path.parent / f'{test_path.name} 题目校对.md',
    ]
    review = None
    for c in candidates:
        if c.exists():
            review = c
            break
    if not review:
        return {}
    text = review.read_text(encoding='utf-8')
    opts = {}
    current_q = None
    for line in text.split('\n'):
        m = re.match(r'### Q(\d+)', line)
        if m:
            current_q = int(m.group(1))
            opts[current_q] = {}
        if current_q:
            om = re.match(r'- \[[ x]\]\s*([A-E])\.\s*(.+)', line)
            if om:
                opts[current_q][om.group(1)] = om.group(2).strip().strip('\"\'"「」')
    return opts


def normalize_vote(vote, qnum, options_map):
    """统一归一化为字母，便于跨源比较"""
    if not vote or vote in ('-', '?', '(空)', ''):
        return None
    opt_map = options_map.get(qnum, {})
    # 已经是纯字母
    if re.match(r'^[A-E]+$', vote):
        return ''.join(sorted(vote))
    # 文字答案 → 匹配选项文本 → 字母（取最长匹配）
    # 去引号，避免 "爱国者" ≠ 爱国者
    clean_vote = vote.strip('\"\'“”「」')
    matches = []
    for letter, text in sorted(opt_map.items()):
        clean_text = text.strip('\"\'“”「」。，、 ')
        if clean_text in clean_vote or clean_vote in clean_text:
            matches.append((len(clean_text), letter))
        elif len(clean_text) >= 8 and clean_text[:8] in clean_vote:
            matches.append((len(clean_text[:8]), letter))
    if matches:
        # 只取一个时用最长匹配；多个时全部保留（多选题选项长度差异大）
        if len(matches) == 1:
            return matches[0][1]
        matches.sort(reverse=True)
        best_len = matches[0][0]
        # 如果最佳匹配远长于其他（>2x），只取最佳
        if len(matches) > 1 and best_len > matches[1][0] * 2:
            return matches[0][1]
        return ''.join(sorted(m[1] for m in matches))
    # 判断题
    if '对' in vote or '正确' in vote:
        return '对'
    if '错' in vote or '错误' in vote:
        return '错'
    # 兜底：清理后返回
    return vote.replace(', ', '').replace('，', '').replace(' ', '')[:20]


def crosscheck(test_dir, config=None):
    """主入口：交叉验证指定测试目录

    Args:
        test_dir: 测试目录路径（如 '测试5' 或 'D:/.../测试5'）
        config: 配置 dict（可选，用于题库路径）

    Returns:
        str: 终版答案 Markdown 文本
    """
    test_path = Path(test_dir)
    if not test_path.exists():
        test_path = Path('D:/Documents/25262/基于题海api的网测助手') / test_dir
    if not test_path.exists():
        raise FileNotFoundError(f"测试目录不存在: {test_dir}")

    # 1. 加载各源
    vault_root = Path('D:/Documents/25262')
    sources = {}
    # Pipeline 答案（可能在测试目录或 vault 根）
    pipeline_candidates = [
        test_path / '答案.md',
        vault_root / f'{test_path.name} 答案.md',
    ]
    for pf in pipeline_candidates:
        if pf.exists():
            sources['pipeline'] = parse_md_answers(str(pf))
            break

    # AI 答案（可能在测试目录或 vault 根，支持 *1.md 变体）
    for name in ['DeepSeek', 'Gemini', '豆包']:
        found = False
        # *1.md 优先（新测试），然后 * .md（旧测试）
        for variant in [f'{name}1.md', f'{name}.md']:
            for loc in [test_path / variant, vault_root / variant]:
                if loc.exists():
                    sources[name] = parse_md_answers(str(loc))
                    found = True
                    break
            if found:
                break

    # 本地题库
    bank_path = test_path.parent / 'wby题库.md' if test_path.parent.name != '25262' else test_path / '..' / 'wby题库.md'
    bank_answers = {}
    if bank_path.exists():
        bank_text = bank_path.read_text(encoding='utf-8')
        # 提取加粗答案
        for line in bank_text.split('\n'):
            if line.strip().startswith('*'):
                bolds = re.findall(r'\*\*(.+?)\*\*', line)
                if bolds:
                    # 用题干关键词做索引
                    clean = re.sub(r'\*\*.*?\*\*', '', line).strip('* 。，、')
                    if len(clean) > 6:
                        bank_answers[clean[:30]] = bolds

    # 2. 加载选项映射
    options_map = load_options(test_path)

    # 3. 逐题判定
    all_qs = set()
    for src in sources.values():
        all_qs.update(src.keys())
    all_qs = sorted(all_qs)

    # 3. 生成输出
    test_name = test_path.name
    lines = []
    lines.append(f"# {test_name} 终版答案\n")
    lines.append(f"> 六源交叉验证：itihey题库 + DeepSeek + Gemini + 豆包\n")
    lines.append(f"> 🟢 全票通过 | 🟡 题库/AI分歧(AI一致则采用AI) | 🔴 各方不一需人工\n")
    lines.append(f"> ⚠️ 本地题库(wby/课本)仅做兜底验证，不作为答案依据\n")
    lines.append("")
    lines.append("## 速查表\n")
    lines.append("| 题号 | 答案 | 置信 | 判定 | 来源 |")
    lines.append("| --- | --- | --- | --- | --- |")

    details = []
    details.append("\n## 逐题说明\n")

    for q in all_qs:
        votes = {}
        for name, src in sources.items():
            if q in src:
                raw = fmt(src[q])
                if raw and raw not in ('-', '?', '(空)', ''):
                    normalized = normalize_vote(raw, q, options_map)
                    if normalized:
                        votes[name] = normalized

        if not votes:
            lines.append(f"| {q} | — | — | 🔴 | 无数据 |")
            details.append(f"### Q{q}\n> ❌ 所有源均无此题数据。\n")
            continue

        # 计数
        ai_votes = {k: v for k, v in votes.items() if k in ('DeepSeek', 'Gemini', '豆包')}
        pipeline_vote = votes.get('pipeline', None)

        ai_values = list(ai_votes.values())
        ai_consensus = None
        if len(set(ai_values)) == 1 and len(ai_values) >= 2:
            ai_consensus = ai_values[0]
        elif len(ai_values) >= 2:
            # 多数投票
            cnt = Counter(ai_values)
            most = cnt.most_common(1)[0]
            if most[1] >= 2:
                ai_consensus = most[0]

        # 判定（新规则）
        # 🟢 题库 + 3AI 全票通过
        # 🟡 题库 vs AI 不一致，但 AI 内部一致 → AI共识
        # 🔴 各方均不一致 → 人工确认
        answer = None
        conf = 0
        judge = ''
        detail = ''

        # 检测 pipeline 答案是否匹配选项
        pl_valid = pipeline_vote and (
            re.match(r'^[A-E]+$', pipeline_vote)
            or len(pipeline_vote) <= 5
        )

        if pipeline_vote and ai_consensus and pipeline_vote == ai_consensus:
            # 🟢 题库+AI全票
            answer = pipeline_vote
            conf = 0.98
            judge = '🟢'
            detail = f"全票通过：题库={pipeline_vote}，AI一致={ai_consensus}"

        elif pipeline_vote and ai_consensus and pipeline_vote != ai_consensus:
            # 题库 与 AI 一致 不一致，但 AI 内部一致 → 🟡 采用 AI 共识
            answer = ai_consensus
            conf = 0.85
            judge = '🟡'
            detail = f"题库={pipeline_vote}，但AI一致={ai_consensus}。采用AI共识"

        elif pipeline_vote and len(ai_values) >= 2:
            # AI 内部不一致，用大多数
            cnt = Counter(ai_values)
            top = cnt.most_common()
            if top[0][1] >= 2:
                # AI 大多数一致，但题库不同
                answer = top[0][0]
                conf = 0.70
                judge = '🔴'
                detail = f"题库={pipeline_vote}，AI分歧({', '.join(f'{k}={v}' for k,v in ai_votes.items())})"
            else:
                answer = pipeline_vote
                conf = 0.60
                judge = '🔴'
                detail = f"各方均不一致。题库={pipeline_vote}，AI分歧({', '.join(f'{k}={v}' for k,v in ai_votes.items())})"

        elif pipeline_vote:
            # 仅题库命中，无 AI 覆盖
            answer = pipeline_vote
            conf = 0.80
            judge = '🟡'
            detail = f"仅题库命中：{pipeline_vote}"

        elif ai_consensus and len(ai_values) >= 2:
            # AI 共识，无题库
            answer = ai_consensus
            conf = 0.80
            judge = '🟡'
            detail = f"AI共识（{'/'.join(ai_votes.keys())}一致={ai_consensus}），无题库覆盖"

        elif len(ai_values) >= 1:
            # AI 分歧，无题库
            cnt = Counter(ai_values)
            top = cnt.most_common()
            answer = top[0][0] if top else list(ai_values)[0]
            conf = 0.50
            judge = '🔴'
            detail = f"无题库，AI分歧({', '.join(f'{k}={v}' for k,v in ai_votes.items())})"

        else:
            answer = '?'
            conf = 0
            judge = '🔴'
            detail = "无有效答案源"

        # 银行命中增强
        bank_hit = False
        for kw, ans_list in bank_answers.items():
            if any(str(q) in kw for _ in [1]):  # 简化检查
                bank_hit = True
                break

        source_label = f"题库({len(votes)}源)" if pipeline_vote else f"AI({len(ai_votes)}源)"
        qlink = "[[题目校对#^Q" + str(q) + "\\|Q" + str(q) + "]]"
        lines.append(f"| {qlink} | {answer} | {conf:.0%} | {judge} | {source_label} |")

        details.append(f"### Q{q}\n")
        details.append(f"**答案**: {answer} | **置信**: {conf:.0%} | **判定**: {judge}\n")
        details.append(f"> {detail}\n")
        if ai_votes:
            details.append(f"> AI投票: " + " | ".join(f"{k}={v}" for k, v in sorted(ai_votes.items())) + "\n")
        if pipeline_vote:
            details.append(f"> Pipeline: {pipeline_vote} (来源: {sources.get('pipeline', {}).get(q, {}).get('source', '?') if isinstance(sources.get('pipeline', {}).get(q, {}), dict) else 'N/A'})\n")
        if bank_hit:
            details.append(f"> 📋 本地题库有相关条目\n")

    lines.extend(details)

    # 统计（只统计速查表行）
    greens = sum(1 for l in lines if l.startswith('| ') and '🟢' in l)
    yellows = sum(1 for l in lines if l.startswith('| ') and '🟡' in l)
    reds = sum(1 for l in lines if l.startswith('| ') and '🔴' in l)
    lines.insert(5, f"> 统计: 🟢{greens} 🟡{yellows} 🔴{reds} | 共 {len(all_qs)} 题\n")

    output = '\n'.join(lines)

    # 保存
    out_path = test_path / '终版答案.md'
    out_path.write_text(output, encoding='utf-8')
    print(f"终版答案 → {out_path}")
    print(f"统计: 🟢{greens} 🟡{yellows} 🔴{reds}")

    return output


if __name__ == '__main__':
    import sys
    test = sys.argv[1] if len(sys.argv) > 1 else '测试5'
    crosscheck(test)

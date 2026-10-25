"""Local auxiliary keywords and cosine neighbours; never creates graph edges or groups."""
import logging, math, re, warnings
from collections import Counter
from .similarity import clean_note
MINIMUM_SCORE = 0.56
MAX_RESULTS = 6
_ADDED_TERMS = set()
STOP = set('这个 那个 一个 一些 我们 自己 什么 怎么 为什么 为啥 如何 就是 不是 时候 需要 通过 然后 进行 可以 可能 已经 知道 不断 相当于 本质上 真正 负责 让它 比较 最后 每次 多少 每个 通常 强调 得到 使用 很多 更快 更慢 更好 变小 变大 一定 应该 只能 这样 那样 还是 每步 以及 如果 因为 所以 对于 其中 现在 没有 不能 不会 一次 一种 一般 是否 或者 而且 这些 那些 我 你 它 这 那 东西 本质 重要 整体 一词 后面 全部 结果 过程 无法 第一波 the and for with this that from into are was were have has not but its can'.split())

def extract_keywords(graph):
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', SyntaxWarning)
        import jieba, jieba.posseg
    jieba.setLogLevel(logging.ERROR)
    technical = set('学习率 优化器 函数名 类名 复利效应 大模型 反向传播 前向传播 状态更新 清仓 减仓 降仓 建仓 满仓 抄底 接盘 调参 亏损 买入理由'.split())
    for term in technical:
        if term not in _ADDED_TERMS:
            jieba.add_word(term, tag='n')
            _ADDED_TERMS.add(term)
    for node in graph['nodes']:
        for term in node.get('tagNames', []):
            if term not in _ADDED_TERMS and re.fullmatch('[\\u4e00-\\u9fff]{2,12}', term):
                jieba.add_word(term, tag='n')
                _ADDED_TERMS.add(term)
    nodes = [dict(n) for n in graph['nodes']]
    docs = []
    spellings = {}

    def terms(text, allow_claim=False):
        out = Counter()
        for token, flag in jieba.posseg.lcut(text):
            token = token.strip()
            key = token.casefold()
            if not (flag.startswith('n') or flag in ('eng', 'vn') or token in technical or (allow_claim and flag[0] in ('a', 'v', 'l', 'i'))):
                continue
            if 2 <= len(token) <= 24 and key not in STOP and re.fullmatch('[A-Za-z][A-Za-z0-9_+.-]*|[\\u4e00-\\u9fff]+', token):
                spellings.setdefault(key, token)
                weight = 1 if flag.startswith('n') or flag in ('eng', 'vn') or token in technical else 1.1 if re.search('[不没无难失错]', token) else 0.85 if flag[0] in ('l', 'i') else 0.7 if flag.startswith('a') else 0.2
                out[key] += weight
        return out
    for node in nodes:
        title, body = clean_note(node)
        docs.append((terms(title, allow_claim=True), terms(body)))
    frequency = Counter((term for title, body in docs for term in set(title) | set(body)))
    for node, (title, body) in zip(nodes, docs):
        scores = {term: (3 * min(title[term], 2) + math.log1p(body[term])) * (1 + math.log((len(nodes) + 1) / (frequency[term] + 1))) for term in set(title) | set(body)}
        chosen = []
        for term in sorted(scores, key=lambda t: (-int(t in title), -scores[t], -len(t), t)):
            if scores[term] < max(scores.values()) * 0.35:
                continue
            if any((term in t or t in term for t in chosen)):
                continue
            chosen.append(term)
            if len(chosen) == 4:
                break
        node['keywords'] = [spellings[t] for t in chosen]
    result = {**graph, 'nodes': nodes, 'keywordExtraction': {'method': 'local-title-claim-tfidf-v3', 'maximumTerms': 4}}
    return result

def enrich_graph(graph):
    try:
        result = extract_keywords(graph)
    except Exception as error:
        logging.getLogger(__name__).warning('关键词暂不可用：%s', type(error).__name__)
        result = {**graph, 'nodes': [{**n, 'keywords': []} for n in graph['nodes']], 'keywordExtraction': {'status': 'unavailable'}}
    nodes = result['nodes']
    semantic = graph.get('semantic', {})
    if semantic.get('status') == 'ready':
        index = {nid: i for i, nid in enumerate(semantic['ids'])}
        values = semantic['values']
        related = {}
        for node in nodes:
            row = index.get(node['id'])
            candidates = []
            if row is not None:
                for other in nodes:
                    if other['id'] == node['id'] or other.get('kind') == 'Topic':
                        continue
                    column = index.get(other['id'])
                    score = values[row][column] if column is not None else None
                    if isinstance(score, (int, float)) and math.isfinite(score) and (score >= MINIMUM_SCORE):
                        candidates.append({'id': other['id'], 'score': float(score)})
            ranked = sorted(candidates, key=lambda n: (-n['score'], n['id']))
            related[node['id']] = ranked[:MAX_RESULTS]
        result['semantic'] = {**semantic, 'relatedStatus': 'ready', 'relatedNotes': related, 'relatedPolicy': {'version': 'cosine-related-notes-v3', 'minimumScore': MINIMUM_SCORE, 'maximumResults': MAX_RESULTS}}
    return result

def safe_enrich_graph(graph):
    """Auxiliary metadata must never block cache recovery or note synchronization."""
    try:
        return enrich_graph(graph)
    except Exception as error:
        logging.getLogger(__name__).warning('辅助信息暂不可用：%s', type(error).__name__)
        semantic = {key: value for key, value in graph.get('semantic', {}).items() if key not in ('relatedNotes', 'relatedPolicy')}
        return {**graph, 'nodes': [{**n, 'keywords': []} for n in graph['nodes']], 'semantic': {**semantic, 'relatedStatus': 'unavailable'}, 'keywordExtraction': {'status': 'unavailable'}}

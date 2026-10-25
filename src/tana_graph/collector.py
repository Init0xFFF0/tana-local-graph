"""Read-only Tana bridge for the existing HTML graph. Local semantic embeddings; Tana access remains read-only."""
import concurrent.futures, hashlib, html, json, re, threading, time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo
from .semantic_enrichment import safe_enrich_graph
ROOT = Path(__file__).resolve().parent
READ_TOOLS = {'list_workspaces', 'list_tags', 'get_children', 'read_node', 'get_tag_schema'}
REF = re.compile('<span\\b[^>]*data-inlineref-node=["\\x27]([^"\\x27]+)["\\x27][^>]*>.*?</span>', re.S)

def plain(text):
    return html.unescape(re.sub('<[^>]*>', '', text))

def js_json(value):
    return json.dumps(value, ensure_ascii=False).replace('<', '\\u003c')

def embedded(text, prefix):
    if prefix not in text:
        prefix = prefix.replace('const ', 'let ', 1)
    if prefix not in text:
        prefix = re.sub('^(const|let) ', '', prefix)
    start = text.index(prefix) + len(prefix)
    value, size = json.JSONDecoder().raw_decode(text[start:])
    return (value, start, start + size)

def replace_data(text, prefix, value):
    _, start, end = embedded(text, prefix)
    return text[:start] + js_json(value) + text[end:]
from .mcp_client import Tana

def parse_fields(text):
    result = []
    for m in re.finditer('^- \\*\\*(.*?)\\*\\* \\(id:([^)]*)\\):: ([^\\n]*)([\\s\\S]*?)(?=^- \\*\\*|\\Z)', text, re.M):
        name, fid, kind, details = m.groups()
        typ = 'instance' if kind.startswith('Instance of') else {'Options': 'options', 'URL': 'url', 'Content': 'content', 'Plain': 'content', 'Number': 'number', 'Date': 'date', 'Email': 'email'}.get(kind.split(' =')[0], 'content')
        field = {'fid': fid, 'label': name, 'type': typ}
        target = re.search('Instance of .*?\\(id:([^)]*)\\)', kind)
        if target:
            field['target'] = target[1]
        options = re.findall('^  - (.*?) \\(id:([^)]*)\\)', details, re.M)
        if options:
            field['options'] = [name for name, _ in options]
        default = re.search('^  - (.*?) \\(id:[^)]*\\) \\*\\[default\\]\\*', details, re.M)
        if default:
            field['default'] = default[1]
        description = re.search('<description>(.*?)</description>', details, re.S)
        if description:
            field['description'] = plain(description[1])
        result.append(field)
    return result

def outline_node_references(root_id, body, visible_ids):
    text = re.sub('```[\\s\\S]*?```', '', body)
    owners = [(-1, root_id)]
    field_indent = None
    result = set()
    for line in text.splitlines():
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if field_indent is not None:
            if indent > field_indent:
                continue
            field_indent = None
        while len(owners) > 1 and owners[-1][0] >= indent:
            owners.pop()
        if re.match('^\\s*-\\s*\\*\\*[^*]+\\*\\*:', line):
            field_indent = indent
            continue
        node_id = re.search('<!--\\s*node-id:\\s*([^\\s>]+)\\s*-->', line)
        if node_id and node_id[1] in visible_ids:
            owners.append((indent, node_id[1]))
        reference = re.fullmatch('\\s*-\\s+\\[[^\\]]+\\]\\(tana:([^\\s)]+)\\)\\s*', line)
        if reference:
            owner, target = (owners[-1][1], reference[1])
            if owner != target and target in visible_ids:
                result.add((owner, target))
    return result

def reference_owners(nid, parent_links, visible_ids):
    owners = set()
    pending = [nid]
    seen = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        if current in visible_ids:
            owners.add(current)
        else:
            pending.extend(parent_links.get(current, ()))
    return owners

def visible_note_ids(raw, parents, roots):
    return {nid for nid, node in raw.items() if not node.get('inTrash') and node.get('docType') in ('content', None) and plain(node.get('name', '')).strip() and node.get('tagIds')}

def tagged_graph(graph):
    nodes = graph['nodes']
    indices = [i for i, n in enumerate(nodes) if n.get('tagIds')]
    if len(indices) == len(nodes):
        return graph
    selected = [nodes[i] for i in indices]
    ids = {n['id'] for n in selected}
    result = {**graph, 'nodes': selected, 'edges': [e for e in graph['edges'] if e['source'] in ids and e['target'] in ids]}
    semantic = graph.get('semantic')
    if semantic and 'ids' in semantic and ('values' in semantic):
        order = {nid: i for i, nid in enumerate(semantic['ids'])}
        positions = [order[n['id']] for n in selected]
        result['semantic'] = {**semantic, 'ids': [n['id'] for n in selected], 'values': [[semantic['values'][i][j] for j in positions] for i in positions]}
        if 'layout' in semantic:
            result['semantic']['layout'] = {**semantic['layout'], 'positions': {nid: point for nid, point in semantic['layout'].get('positions', {}).items() if nid in ids}}
    return result

class Graph:

    def __init__(self, source, tana, settings):
        from .semantic_layout import SemanticLayout
        self.settings = settings
        self.tana = tana
        self.views = embedded(source, 'const views=')[0]
        self.seed = embedded(source, 'const graphData=')[0]
        self.seed_schema = embedded(source, 'const schemaData=')[0]
        self.workspace = settings.workspace_id
        self.tag_ids = {t['tanaId']: t['id'] for t in self.seed_schema['tags']}
        self.field_ids = {f['tanaId']: f['id'] for f in self.seed_schema['fields']}
        self.cache = None
        self.checked = 0
        self.lock = threading.Lock()
        self.semantic = None
        self.layout = SemanticLayout()

    def collect(self):
        api = self.tana
        workspaces = api.call('list_workspaces', {})
        workspace = next((w for w in workspaces if w['id'] == self.workspace), None)
        if not workspace:
            raise RuntimeError('原图谱的工作区暂不可用')
        tags = api.call('list_tags', {'workspaceId': self.workspace, 'limit': 200})
        if len(tags) >= 200:
            raise RuntimeError('标签达到读取上限，拒绝发布可能不完整的数据')
        tag_names = {t['id']: t['name'] for t in tags}
        roots = list(self.settings.root_ids)
        raw = {}
        parents = {}
        parent_links = {}
        tuples = []
        frontier = list(roots)
        seen = set()
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            while frontier:
                batch = list(dict.fromkeys((nid for nid in frontier if nid not in seen)))
                frontier = []
                seen.update(batch)
                if len(seen) > 2000:
                    raise RuntimeError('读取范围超过上限，保留上次完整图谱')
                for owner, children in zip(batch, pool.map(api.children, batch)):
                    for child in children:
                        if child.get('inTrash') or child['id'] in self.settings.exclude_ids:
                            continue
                        if child.get('docType') == 'tuple':
                            tuples.append((owner, child['id']))
                            continue
                        if child.get('docType') not in ('content', 'calendar', 'calendarNode', 'journalPart', 'codeblock', None):
                            continue
                        nid = child['id']
                        raw.setdefault(nid, child)
                        parents.setdefault(nid, owner)
                        parent_links.setdefault(nid, set()).add(owner)
                        if child.get('childCount', 0) and child.get('docType') != 'codeblock':
                            frontier.append(nid)
            values = {}
            for (owner, _), children in zip(tuples, pool.map(api.children, [tid for _, tid in tuples])):
                attr = next((c for c in children if c.get('docType') == 'attrDef'), None)
                if attr:
                    values.setdefault(owner, {})[attr['id']] = [{'id': c['id'], 'name': plain(c['name'])} for c in children if c['id'] != attr['id'] and (not c.get('inTrash'))]
            ids = visible_note_ids(raw, parents, set(roots))
            wanted = {tid for nid in ids for tid in raw[nid].get('tagIds', []) if tid in tag_names}
            schemas = {}
            pending = wanted
            while pending:
                batch = sorted(pending - set(schemas))
                pending = set()
                if not batch:
                    break
                if len(schemas) + len(batch) > 200:
                    raise RuntimeError('标签范围过大，保留上次完整图谱')
                texts = list(pool.map(lambda tid: api.call('get_tag_schema', {'tagId': tid, 'includeInheritedFields': False}), batch))
                for tid, text in zip(batch, texts):
                    name = re.search('# Tag definition: (.*?) \\(id:', text)
                    parents_tag = re.findall('^Extends #[^\\n]*?\\(id:([^)]*)\\)', text, re.M)
                    fields = parse_fields(text)
                    schemas[tid] = {'name': name[1] if name else tag_names.get(tid, tid), 'body': text, 'parents': parents_tag, 'fields': fields}
                    pending.update(parents_tag)
                    pending.update((f['target'] for f in fields if f.get('target')))
            tid = lambda real: self.tag_ids.get(real, 'tag-' + real)
            fid = lambda real: self.field_ids.get(real, 'field-' + real)
            schema = {k: [] for k in ['tags', 'fields', 'extends', 'hasField', 'instanceOf']}
            field_seen = set()
            for real, tag in schemas.items():
                schema['tags'].append({'id': tid(real), 'tanaId': real, 'name': tag['name'], 'body': tag['body'], 'system': real.startswith('SYS_')})
                schema['extends'].extend(({'from': tid(real), 'to': tid(parent)} for parent in tag['parents'] if parent in schemas))
                for f in tag['fields']:
                    schema['hasField'].append({'tag': tid(real), 'field': fid(f['fid'])})
                    if f['fid'] in field_seen:
                        continue
                    field_seen.add(f['fid'])
                    item = {'id': fid(f['fid']), 'tanaId': f['fid'], 'name': f['label'] + ' · ' + tag['name'] if f['label'] in ('Topic', 'Parent', 'Status', 'Type') else f['label'], 'type': f['type'], **{k: f[k] for k in ('options', 'default', 'description') if k in f}}
                    item['body'] = '字段：' + f['label'] + '\n类型：' + f['type']
                    schema['fields'].append(item)
                    if f.get('target') in schemas:
                        schema['instanceOf'].append({'field': fid(f['fid']), 'tag': tid(f['target'])})

            def inherited(real, trail=None):
                trail = set(trail or ())
                if real in trail or real not in schemas:
                    return {}
                trail.add(real)
                out = {}
                for parent in schemas[real]['parents']:
                    out.update(inherited(parent, trail))
                out.update({f['fid']: f for f in schemas[real]['fields']})
                return out
            positions = {n['id']: (n['x'], n['y']) for n in self.seed['nodes']}
            nodes = []
            bodies = list(pool.map(lambda nid: api.call('read_node', {'nodeId': nid, 'maxDepth': 10}), sorted(ids)))
            for i, (nid, body) in enumerate(zip(sorted(ids), bodies)):
                n = raw[nid]
                kind = next((tag_names[t] for t in n.get('tagIds', []) if tag_names.get(t) in ['Topic', 'Question', 'Note', 'Idea', 'Mistake', 'Book', 'Source', 'Video', 'Article']), 'Note' if not n.get('tagIds') else '节点')
                fields = {}
                for tag in n.get('tagIds', []):
                    fields.update(inherited(tag))
                status = next((v[0]['name'] for key, v in values.get(nid, {}).items() if v and fields.get(key, {}).get('label') == 'Status'), '')
                x, y = positions.get(nid, (190 + i % 7 * 210, 210 + i // 7 % 7 * 170))
                name = REF.sub(lambda m: plain(raw.get(m[1], {}).get('name', '引用节点')), n['name'])
                nodes.append({'id': nid, 'name': plain(name), 'kind': kind, 'status': status, 'body': body, 'x': x, 'y': y, 'tagIds': list(n.get('tagIds', [])), 'tagNames': [tag_names.get(t, t) for t in n.get('tagIds', [])]})
            node_references = set().union(*(outline_node_references(n['id'], n['body'], ids) for n in nodes))
            edges = []
            keys = set()

            def edge(a, b, typ, evidence):
                if a != b and a in ids and (b in ids) and ((a, b, typ) not in keys):
                    keys.add((a, b, typ))
                    edges.append({'source': a, 'target': b, 'type': typ, 'evidence': evidence})

            def owner(nid):
                visited = set()
                while nid not in ids and nid not in visited:
                    visited.add(nid)
                    nid = parents.get(nid)
                return nid if nid in ids else None
            for source, target in sorted(node_references):
                edge(source, target, 'ref', '节点引用')
            for nid, n in raw.items():
                if n.get('docType') == 'codeblock':
                    continue
                targets = REF.findall(n['name'])
                if targets:
                    for root in sorted(reference_owners(nid, parent_links, ids)):
                        for target in targets:
                            edge(root, target, 'ref', plain(n['name']))
            for n in nodes:
                fields = {}
                for tag in raw[n['id']].get('tagIds', []):
                    fields.update(inherited(tag))
                for key, field in fields.items():
                    if field['label'] in ('Topic', 'Parent') and field['type'] == 'instance':
                        for value in values.get(n['id'], {}).get(key, []):
                            edge(n['id'], value['id'], 'topic' if field['label'] == 'Topic' else 'parent', field['label'])
                parent = owner(parents.get(n['id']))
                if parent and (parent, n['id']) not in node_references:
                    edge(parent, n['id'], 'contains', '原始父子层级')
        graph = {'nodes': nodes, 'edges': edges, 'availableTags': [{'id': t['id'], 'name': t['name']} for t in tags if not t['id'].startswith('SYS_')], 'scope': self.seed.get('scope', '当前 Tana 工作区')}
        schema['metadata'] = {'workspaceId': self.workspace}
        try:
            if self.semantic is None:
                from .similarity import SemanticIndex
                self.semantic = SemanticIndex(self.settings.data_dir, self.settings.model_path)
            graph['semantic'] = self.semantic.build(nodes)
            positions = self.layout.build(nodes, edges, graph['semantic'])
            for node in nodes:
                node.update(positions[node['id']])
            graph['semantic']['layout'] = {'method': 'cosine-distance-mds', 'positions': positions}
        except Exception as error:
            print('本地语义布局不可用：' + type(error).__name__, flush=True)
            graph['semantic'] = {'status': 'unavailable'}
        graph = safe_enrich_graph(graph)
        revision = hashlib.sha256(json.dumps([graph, schema], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        views = {'content': replace_data(self.views['content'], 'const data=', graph), 'schema': replace_data(self.views['schema'], 'const schema = ', schema)}
        return {'graph': graph, 'schema': schema, 'views': views, 'workspace': workspace['name'], 'observedAt': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), 'revision': revision}

    def latest(self, force=False):
        with self.lock:
            if force or self.cache is None or time.monotonic() - self.checked >= 8:
                result = self.collect()
                self.cache = result
                self.checked = time.monotonic()
            return self.cache

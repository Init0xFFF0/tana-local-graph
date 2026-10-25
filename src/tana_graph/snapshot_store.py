"""Atomic, data-only last-success snapshot. Page code always comes from the current template."""
import json, math, os, tempfile
from pathlib import Path

class SnapshotStore:

    def __init__(self, path, workspace, app):
        self.path = Path(path)
        self.workspace = workspace
        self.app = app

    def validate(self, payload):
        graph = payload['graph']
        schema = payload['schema']
        nodes = graph['nodes']
        edges = graph['edges']
        semantic = graph['semantic']
        if schema.get('metadata', {}).get('workspaceId') != self.workspace:
            raise ValueError('snapshot workspace mismatch')
        if not isinstance(nodes, list) or not isinstance(edges, list) or semantic.get('status') != 'ready':
            raise ValueError('invalid graph snapshot')
        ids = [n['id'] for n in nodes]
        if len(set(ids)) != len(ids) or semantic['ids'] != ids:
            raise ValueError('invalid node identities')
        matrix = semantic['values']
        if len(matrix) != len(ids) or any((len(row) != len(ids) or any((not isinstance(v, (float, int)) or not math.isfinite(v) or (not -1.00001 <= v <= 1.00001) for v in row)) for row in matrix)):
            raise ValueError('invalid similarity matrix')
        if any((e['source'] not in set(ids) or e['target'] not in set(ids) for e in edges)):
            raise ValueError('invalid edge endpoint')
        if not all((isinstance(schema.get(key), list) for key in ('tags', 'fields', 'extends', 'hasField', 'instanceOf'))):
            raise ValueError('invalid schema')
        if not isinstance(payload.get('revision'), str) or not isinstance(payload.get('observedAt'), str):
            raise ValueError('missing snapshot provenance')
        return payload

    def load(self):
        try:
            saved = json.loads(self.path.read_text())
            if saved.get('app') != self.app:
                return None
            return self.validate(saved['snapshot'])
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return None

    def save(self, payload):
        data = {key: payload[key] for key in ('graph', 'schema', 'workspace', 'observedAt', 'revision')}
        self.validate(data)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf8', dir=self.path.parent, prefix='.last-good-', suffix='.json', delete=False) as file:
                temporary = Path(file.name)
                json.dump({'app': self.app, 'snapshot': data}, file, ensure_ascii=False, allow_nan=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()

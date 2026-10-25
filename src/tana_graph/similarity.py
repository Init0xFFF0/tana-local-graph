from pathlib import Path
import hashlib, html, json, re, sqlite3, os
MODEL = 'Qwen/Qwen3-Embedding-0.6B'
REVISION = '97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3'
PIPELINE = 'qwen-title-body-chunks-v2'
PROMPT = 'Instruct: Represent this personal note for grouping notes about the same topic or concept.\nQuery: '

def clean_note(node):
    title = html.unescape(re.sub('<[^>]*>', '', node.get('name', ''))).strip()
    body = re.sub('```[\\s\\S]*?```', '', node.get('body', ''))
    lines = body.splitlines()[1:]
    lines = [line for line in lines if not re.match('^\\s*-?\\s*\\*{0,2}(Topic|Parent|Status|Type)\\*{0,2}:', line)]
    body = '\n'.join(lines)
    body = re.sub('\\[([^\\]]+)\\]\\([^)]*\\)', '\\1', body)
    body = html.unescape(re.sub('<[^>]*>', '', body))
    body = re.sub('[#*`]|^\\s*-\\s*', '', body, flags=re.M)
    body = re.sub('\\s+', ' ', body).strip()
    return (title, body)

class SemanticIndex:

    def __init__(self, root, model_path):
        self.model_path = str(model_path)
        self.root = Path(root)
        self.model = None
        self.computations = 0
        self.db = sqlite3.connect(self.root / 'embeddings.sqlite3', check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS embeddings (key TEXT PRIMARY KEY, vector TEXT NOT NULL)')

    def build(self, nodes):
        import numpy as np
        vectors = []
        pending = []
        keys = []
        for node in nodes:
            title, body = clean_note(node)
            key = hashlib.sha256(json.dumps([MODEL, REVISION, PIPELINE, PROMPT, title, body], ensure_ascii=False).encode()).hexdigest()
            keys.append(key)
            row = self.db.execute('SELECT vector FROM embeddings WHERE key=?', (key,)).fetchone()
            vector = np.array(json.loads(row[0]), dtype=np.float32) if row else None
            if vector is not None and (vector.shape != (1024,) or not np.isfinite(vector).all() or abs(float(np.linalg.norm(vector)) - 1) > 0.01):
                vector = None
            vectors.append(vector)
            if vector is None:
                pending.append((len(vectors) - 1, title, body))
        if pending:
            if self.model is None:
                import torch
                from sentence_transformers import SentenceTransformer
                torch.set_num_threads(2)
                model_path = self.model_path
                self.model = SentenceTransformer(model_path, device='cpu', local_files_only=True, trust_remote_code=False, model_kwargs={'dtype': torch.float32, 'attn_implementation': 'sdpa'}, processor_kwargs={'padding_side': 'left'})
                self.model.max_seq_length = 2048
            parts = []
            ranges = []
            for index, title, body in pending:
                start = len(parts)
                parts.append(title or body[:1200] or '空白笔记')
                parts.extend((body[i:i + 1200] for i in range(0, min(len(body), 8400), 1200)))
                ranges.append((index, start, len(parts)))
            encoded = self.model.encode(parts, batch_size=4, prompt=PROMPT, normalize_embeddings=True, show_progress_bar=False, convert_to_numpy=True)
            for index, start, end in ranges:
                vector = encoded[start].astype(np.float32) if end == start + 1 else encoded[start].astype(np.float32) * 0.65 + encoded[start + 1:end].astype(np.float32).mean(axis=0) * 0.35
                vector /= max(float(np.linalg.norm(vector)), 1e-12)
                vectors[index] = vector
                self.db.execute('INSERT OR REPLACE INTO embeddings VALUES (?,?)', (keys[index], json.dumps(vector.tolist())))
            self.db.commit()
            self.computations += len(pending)
        values = np.clip(np.stack(vectors) @ np.stack(vectors).T, -1, 1).round(5).tolist() if vectors else []
        return {'status': 'ready', 'model': MODEL, 'revision': REVISION, 'ids': [n['id'] for n in nodes], 'values': values}

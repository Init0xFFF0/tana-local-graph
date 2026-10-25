"""Deterministic initial layout from cosine distances only."""
import hashlib, json
import numpy as np
from sklearn.manifold import smacof

class SemanticLayout:

    def __init__(self):
        self.key = None
        self.positions = None

    def build(self, nodes, edges, semantic):
        ids = semantic['ids']
        values = semantic['values']
        key = hashlib.sha256(json.dumps([ids, values], sort_keys=True).encode()).hexdigest()
        if key == self.key:
            return self.positions
        count = len(ids)
        scale = max(1, (count / 23) ** 0.5 * 1.38)
        if not count:
            self.key = key
            self.positions = {}
            return self.positions
        scores = np.clip(np.asarray(values, dtype=float), 0, 1)
        low = np.clip((0.6 - scores) / 0.6, 0, 1)
        distances = (260 + 1450 * np.power(1 - scores, 1.5) + 200 * low * low * (3 - 2 * low)) / scale
        np.fill_diagonal(distances, 0)
        index = {nid: i for i, nid in enumerate(ids)}
        if count < 2:
            points = np.zeros((count, 2))
        else:
            points, _ = smacof(distances, metric=True, n_components=2, n_init=4, max_iter=500, eps=1e-06, random_state=42, normalized_stress=True)
        points -= points.mean(axis=0)
        if self.positions:
            common = [nid for nid in ids if nid in self.positions]
            if len(common) >= 3:
                a = np.array([points[index[nid]] for nid in common])
                b = np.array([[self.positions[nid]['x'] - 915, self.positions[nid]['y'] - 715] for nid in common])
                a -= a.mean(axis=0)
                b -= b.mean(axis=0)
                u, _, vt = np.linalg.svd(a.T @ b)
                points = points @ (u @ vt)
        span = np.ptp(points, axis=0)
        fit = min(1, 1570 / max(span[0], 1), 1190 / max(span[1], 1))
        points *= fit
        points -= (points.min(axis=0) + points.max(axis=0)) / 2
        points += np.array([915, 715])
        self.positions = {nid: {'x': round(float(p[0]), 4), 'y': round(float(p[1]), 4)} for nid, p in zip(ids, points)}
        self.key = key
        return self.positions

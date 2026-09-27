"""Step 9: categories, the frequent-complaints view, and spike detection.

Categories are derived by clustering real questions, not invented, then named by
hand. Clustering writes suggested names; a person edits them in
config/categories/<firm>.yaml. Every mail gets the category of its nearest centroid.

A spike is one category's count of *distinct customers* in a short window rising
well above that category's own normal rate. One row per spike, not one per mail;
closed when the rate falls back.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from . import actions
from .config import ROOT, Firm
from .embed import cached

UNANSWERED = "unanswered"   # pseudo-category: mails the tool refused. A flood of these is an incident.

_STOP = set(
    """a an the and or but if to of in on at for from with by is are was were be been am i im i'm
    me my we our you your it its it's this that these those do does did not no can can't cannot could
    would will just so what when where why how who there here get got have has had any all some more
    very really still now today yesterday please thanks thank hi hello hey help us them they he she his
    her one two out up about as than then also too only after before again been being don't dont won't
    didn't doesn't isn't wasn't know need want like going go make let see time day days back new""".split()
)


def _cfg_path(firm: Firm) -> Path:
    return ROOT / "config" / "categories" / f"{firm.key}.yaml"


def _centroid_path(firm: Firm) -> Path:
    """Centroids sit next to the names they belong to, so the two cannot drift apart."""
    return ROOT / "config" / "categories" / f"{firm.key}.npz"


@dataclass
class Category:
    id: int
    name: str
    keywords: list[str]
    examples: list[str]
    common_causes: list[str]


def _keywords(texts: list[str], background: Counter, n_bg: int, k: int = 8) -> list[str]:
    """Words unusually common in this cluster compared with the whole corpus."""
    c = Counter(w for t in texts for w in set(re.findall(r"[a-z][a-z'\-]{2,}", t.lower())) if w not in _STOP)
    n = max(1, len(texts))
    scored = {w: (cnt / n) * np.log(n_bg / (1 + background[w])) for w, cnt in c.items() if cnt >= 3}
    return [w for w, _ in sorted(scored.items(), key=lambda x: -x[1])[:k]]


def cluster(firm: Firm, k: int = 24, seed: int = 0, log=print) -> list[Category]:
    """Cluster first messages that got a real answer; write suggested categories."""
    from sklearn.cluster import MiniBatchKMeans

    conn = sqlite3.connect(firm.db_path)
    df = pd.read_sql_query(
        "SELECT id, question FROM pairs WHERE is_deflection = 0 AND is_followup = 0 ORDER BY id", conn
    )
    conn.close()
    vecs = cached(firm.data_dir / "vec_questions.npz", df.id.tolist(), df.question.tolist())
    km = MiniBatchKMeans(n_clusters=k, random_state=seed, n_init=5, batch_size=2048).fit(vecs)
    labels = km.labels_
    centroids = km.cluster_centers_ / np.linalg.norm(km.cluster_centers_, axis=1, keepdims=True)

    background = Counter(
        w for t in df.question for w in set(re.findall(r"[a-z][a-z'\-]{2,}", t.lower())) if w not in _STOP
    )
    cats = []
    for c in range(k):
        idx = np.where(labels == c)[0]
        texts = df.question.iloc[idx].tolist()
        # Examples: the questions nearest the centre, i.e. the most typical.
        near = idx[np.argsort(-(vecs[idx] @ centroids[c]))[:5]]
        kw = _keywords(texts, background, len(df))
        cats.append(
            Category(c, " / ".join(kw[:3]) or f"cluster {c}", kw, [df.question.iloc[i][:160] for i in near], [])
        )
        log(f"[categories] {c:2d} n={len(idx):5d}  {', '.join(kw[:6])}")

    _centroid_path(firm).parent.mkdir(parents=True, exist_ok=True)
    np.savez(_centroid_path(firm), centroids=centroids.astype(np.float32))
    save(firm, cats, sizes=Counter(labels.tolist()), suggested=True)
    return cats


def save(firm: Firm, cats: list[Category], sizes: Counter | None = None, suggested: bool = False) -> None:
    path = _cfg_path(firm)
    path.parent.mkdir(parents=True, exist_ok=True)
    head = (
        f"# Categories for {firm.name}.\n"
        "# Derived by clustering real questions (python -m inbox categorize). The names below are\n"
        + ("# SUGGESTED from keywords: rename each one by hand; the complaints view uses these names.\n" if suggested else "#\n")
        + "# ids must stay as they are: they match the cluster centroids in the .npz file next to this one.\n\n"
    )
    body = {
        "categories": [
            {
                "id": c.id,
                "name": c.name,
                **({"size": int(sizes[c.id])} if sizes else {}),
                "keywords": c.keywords,
                "examples": c.examples,
                "common_causes": c.common_causes,
            }
            for c in cats
        ]
    }
    path.write_text(head + yaml.safe_dump(body, sort_keys=False, allow_unicode=True, width=110), encoding="utf-8")


def load(firm: Firm) -> tuple[list[Category], np.ndarray]:
    raw = yaml.safe_load(_cfg_path(firm).read_text(encoding="utf-8"))
    cats = [
        Category(c["id"], c["name"], c.get("keywords", []), c.get("examples", []), c.get("common_causes") or [])
        for c in raw["categories"]
    ]
    centroids = np.load(_centroid_path(firm))["centroids"]
    return cats, centroids


def assign(vecs: np.ndarray, centroids: np.ndarray) -> np.ndarray:
    return (vecs @ centroids.T).argmax(axis=1)


# ---- Frequent complaints -----------------------------------------------------------


def complaints(events: pd.DataFrame, names: dict, now: datetime, days: int = 7) -> pd.DataFrame:
    """Distinct customers per category in the last `days`, against the `days` before.

    events: columns ts (datetime), customer_id, category
    """
    cur = events[(events.ts > now - timedelta(days=days)) & (events.ts <= now)]
    prev = events[(events.ts > now - timedelta(days=2 * days)) & (events.ts <= now - timedelta(days=days))]
    a = cur.groupby("category").customer_id.nunique()
    b = prev.groupby("category").customer_id.nunique()
    out = pd.DataFrame({"customers": a, "previous": b}).fillna(0).astype(int)
    out["change"] = out.customers - out.previous
    out["name"] = [names.get(c, str(c)) for c in out.index]
    return out.sort_values("customers", ascending=False)


# ---- Spikes ------------------------------------------------------------------------


@dataclass
class SpikeConfig:
    window_hours: int = 6
    baseline_days: int = 7
    min_customers: int = 8        # never call fewer distinct customers than this a spike
    ratio: float = 3.0            # at least this many times the normal rate for the window
    sigmas: float = 4.0           # and this many standard deviations above it


def detect_spikes(
    events: pd.DataFrame, now: datetime, cfg: SpikeConfig = SpikeConfig()
) -> list[dict]:
    """Categories spiking in the window ending at `now`.

    Counts distinct customers, so one customer writing five times counts once.
    The baseline is the same category's windows over the previous `baseline_days`.
    """
    w = timedelta(hours=cfg.window_hours)
    hist = events[(events.ts > now - timedelta(days=cfg.baseline_days) - w) & (events.ts <= now - w)]
    cur = events[(events.ts > now - w) & (events.ts <= now)]
    n_windows = int(timedelta(days=cfg.baseline_days) / w)
    out = []
    for cat, g in cur.groupby("category"):
        count = g.customer_id.nunique()
        if count < cfg.min_customers:
            continue
        h = hist[hist.category == cat]
        if len(h):
            bins = ((now - w - h.ts) // w).astype(int)
            per = h.groupby(bins).customer_id.nunique().reindex(range(n_windows), fill_value=0)
            mean, std = float(per.mean()), float(per.std(ddof=0))
        else:
            mean, std = 0.0, 0.0
        if count >= max(cfg.ratio * mean, mean + cfg.sigmas * std, cfg.min_customers):
            out.append({"category": cat, "customers": int(count), "normal": round(mean, 1), "since": (now - w)})
    return out


def group_incidents(spikes: list[dict], gap: timedelta) -> list[dict]:
    """Spikes that overlap in time are one incident.

    One real incident (the iOS 11.1 "I" bug) split across eight categories, because
    people describe it in different words. Their centroids could not be told apart
    from unrelated spikes (all 0.94 to 0.99 on this embedding scale), so grouping is
    by time, and the row lists every topic involved.
    """
    out: list[dict] = []
    for s in sorted(spikes, key=lambda x: x["opened"]):
        end = s.get("closed") or s["opened"] + gap
        if out and s["opened"] <= out[-1]["closed"] + gap:
            inc = out[-1]
            inc["categories"].append(s["category"])
            inc["peak"] = max(inc["peak"], s.get("peak", s.get("customers", 0)))
            inc["closed"] = max(inc["closed"], end)
        else:
            out.append({"opened": s["opened"], "closed": end, "categories": [s["category"]],
                        "peak": s.get("peak", s.get("customers", 0))})
    return out


def raise_spikes(
    conn: sqlite3.Connection, spikes: list[dict], names: dict, now: datetime, cfg: SpikeConfig = SpikeConfig()
) -> list[int]:
    """One open action row per incident. Categories spiking together join the open
    row instead of adding rows; the row closes once nothing is spiking."""
    row = conn.execute("SELECT id, what FROM actions WHERE kind='spike' AND status='open' AND rule='spike:incident'").fetchone()
    if not spikes:
        if row:
            conn.execute("UPDATE actions SET status='done', done_at=? WHERE id=?", (now.isoformat(), row["id"]))
        return []
    spikes = sorted(spikes, key=lambda s: -s["customers"])
    topics = ", ".join(f"\"{names.get(s['category'], s['category'])}\" ({s['customers']}, normally {s['normal']})" for s in spikes)
    what = (
        f"Unusual volume in the last {cfg.window_hours}h: {topics}. "
        "Likely one incident. Consider a dated note on the context page."
    )
    if row:
        conn.execute("UPDATE actions SET what=? WHERE id=?", (what, row["id"]))
        return []
    return [actions.add(conn, "spike", what, rule="spike:incident", origin="spike", created_at=now)]

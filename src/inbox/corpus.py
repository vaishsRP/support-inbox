"""Importer for the Customer Support on Twitter corpus (twcs.csv).

The file is 2.8M tweets across ~100 brands and too big to hold in memory with its
text on a small laptop, so it is read twice: once without text to rebuild threads
and find the ones this firm took part in, once in chunks to pull only those rows.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import numpy as np
import pandas as pd

from .config import Firm
from .store import connect
from .textclean import clean, is_deflection, redact

CHUNK = 400_000
TIME_FORMAT = "%a %b %d %H:%M:%S %z %Y"
# A brand reply split over several tweets ("1/2", "2/2") is one answer.
CONTINUATION_WINDOW = timedelta(minutes=10)


def _thread_roots(parent: pd.Series) -> pd.Series:
    """Map every tweet id to the id at the top of its reply chain."""
    root = parent.where(parent.isin(parent.index), parent.index.to_series())
    root = root.fillna(pd.Series(parent.index, index=parent.index))
    for _ in range(200):
        nxt = root.map(root)
        nxt = nxt.fillna(root)
        if nxt.equals(root):
            break
        root = nxt
    return root


def _select_thread_ids(firm: Firm) -> set[int]:
    cols = ["tweet_id", "author_id", "in_response_to_tweet_id"]
    meta = pd.concat(
        pd.read_csv(firm.corpus_path, usecols=cols, dtype={"author_id": "str"}, chunksize=CHUNK)
    )
    meta = meta.drop_duplicates("tweet_id").set_index("tweet_id")
    parent = meta.in_response_to_tweet_id.astype("Int64")
    parent = parent.astype("float64")
    root = _thread_roots(parent.where(parent.notna(), np.nan))
    brand_roots = set(root[meta.author_id == firm.brand_handle].astype("int64"))
    keep = root.astype("int64").isin(brand_roots)
    return set(meta.index[keep.values])


def _load_rows(firm: Firm, ids: set[int]) -> pd.DataFrame:
    parts = []
    for chunk in pd.read_csv(firm.corpus_path, dtype={"author_id": "str"}, chunksize=CHUNK):
        parts.append(chunk[chunk.tweet_id.isin(ids)])
    df = pd.concat(parts).drop_duplicates("tweet_id")
    df["created_at"] = pd.to_datetime(df.created_at, format=TIME_FORMAT, utc=True)
    df["parent_id"] = df.in_response_to_tweet_id.astype("Int64")
    df["text"] = df.text.fillna("")
    return df


def import_twcs(firm: Firm, log=print) -> dict:
    log(f"[import] finding threads involving @{firm.brand_handle}")
    ids = _select_thread_ids(firm)
    log(f"[import] {len(ids):,} tweets in those threads; loading text")
    df = _load_rows(firm, ids)

    parent_of = dict(zip(df.tweet_id, df.parent_id))
    root_cache: dict[int, int] = {}

    def root(tid: int) -> int:
        chain = []
        while tid not in root_cache:
            chain.append(tid)
            p = parent_of.get(tid)
            if p is None or pd.isna(p) or p not in parent_of:
                root_cache[tid] = tid
                break
            tid = int(p)
        r = root_cache[tid]
        for t in chain:
            root_cache[t] = r
        return r

    df["thread_id"] = [root(int(t)) for t in df.tweet_id]
    df["is_brand"] = df.author_id == firm.brand_handle

    conn = connect(firm.db_path)
    with conn:
        conn.execute("DELETE FROM pairs WHERE source = 'corpus'")
        conn.execute("DELETE FROM messages")
        conn.executemany(
            "INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
            (
                (
                    str(r.tweet_id),
                    str(r.thread_id),
                    None if pd.isna(r.parent_id) else str(int(r.parent_id)),
                    r.author_id,
                    int(bool(r.inbound)),
                    r.created_at.isoformat(),
                    r.text,
                )
                for r in df.itertuples()
            ),
        )
    pairs = _build_pairs(df, firm)
    with conn:
        conn.executemany(
            """INSERT INTO pairs (thread_id, customer_msg_id, customer_id, asked_at, answered_at,
               question, answer, answer_raw, is_followup, is_deflection, source)
               VALUES (?,?,?,?,?,?,?,?,?,?, 'corpus')""",
            pairs,
        )
    stats = _stats(conn)
    conn.close()
    log(f"[import] {stats}")
    return stats


def _build_pairs(df: pd.DataFrame, firm: Firm) -> list[tuple]:
    by_id = df.set_index("tweet_id")
    brand = df[df.is_brand & df.parent_id.notna()].sort_values("created_at")
    children: dict[int, list] = {}
    for r in brand.itertuples():
        children.setdefault(int(r.parent_id), []).append(r)

    out = []
    for cust_id, replies in children.items():
        if cust_id not in by_id.index:
            continue
        cust = by_id.loc[cust_id]
        if not bool(cust.inbound):
            continue
        first = replies[0]
        parts, last = [first.text], first
        # Follow a same-brand continuation chain ("1/2" then "2/2").
        while True:
            cont = children.get(int(last.tweet_id))
            if not cont or cont[0].created_at - last.created_at > CONTINUATION_WINDOW:
                break
            last = cont[0]
            parts.append(last.text)
        answer_raw = " ".join(parts)
        question = clean(cust.text)
        answer = redact(clean(answer_raw, keep_urls=True), firm.public_numbers)
        if not question or not answer:
            continue
        out.append(
            (
                str(cust.thread_id),
                str(cust_id),
                cust.author_id,
                cust.created_at.isoformat(),
                first.created_at.isoformat(),
                question,
                answer,
                answer_raw,
                int(not pd.isna(cust.parent_id)),
                int(is_deflection(answer)),
            )
        )
    return out


def _stats(conn: sqlite3.Connection) -> dict:
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "messages": q("SELECT COUNT(*) FROM messages"),
        "threads": q("SELECT COUNT(DISTINCT thread_id) FROM messages"),
        "pairs": q("SELECT COUNT(*) FROM pairs"),
        "deflections": q("SELECT COUNT(*) FROM pairs WHERE is_deflection = 1"),
        "followups": q("SELECT COUNT(*) FROM pairs WHERE is_followup = 1"),
    }

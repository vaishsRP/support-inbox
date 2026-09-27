"""Replay the corpus in time order through the drafter.

What this measures, precisely: each draft is compared with the reply the firm
actually sent. That is similarity to a reference answer, not how far a human
moved the draft (nobody edits anything in a replay). Plotting it against the
pool's size at the time shows whether more examples help. It does not show that
human corrections teach the tool; the hand-run study in the spec covers that.

Only answers from before each mail are searchable. Sampling is spread across
time slices so the curve has something to show.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from rapidfuzz.distance import Indel

from .config import ROOT, Firm
from .draft import Drafter, Incoming, Thresholds
from .feedback import draft_body, measures
from .llm import OpenAICompatible
from .rules import check_outgoing, load_rules
from .store import connect


def _thread(conn: sqlite3.Connection, thread_id: str, before: str, brand: str) -> list[tuple[str, str]]:
    rows = conn.execute(
        "SELECT author, text FROM messages WHERE thread_id = ? AND created_at < ? ORDER BY created_at",
        (thread_id, before),
    ).fetchall()
    return [("firm" if r["author"] == brand else "customer", r["text"]) for r in rows]


def sample(conn: sqlite3.Connection, per_slice: int, slices: int, seed: int) -> pd.DataFrame:
    """Customer messages that got any reply (deflections included: they are real
    incoming mail), spread evenly over time slices."""
    df = pd.read_sql_query(
        "SELECT id, thread_id, customer_msg_id, customer_id, asked_at, question, answer, is_deflection "
        "FROM pairs WHERE source = 'corpus' ORDER BY asked_at",
        conn,
    )
    df = df[df.asked_at >= "2017-10-01"]   # a handful of stray older tweets precede the main window
    edges = np.linspace(0, len(df), slices + 1).astype(int)
    rng = np.random.default_rng(seed)
    parts = []
    for a, b in zip(edges[:-1], edges[1:]):
        idx = rng.choice(np.arange(a, b), size=min(per_slice, b - a), replace=False)
        parts.append(df.iloc[np.sort(idx)].assign(slice=len(parts)))
    return pd.concat(parts)


def run(firm: Firm, per_slice: int = 25, slices: int = 3, seed: int = 3, name: str | None = None, log=print) -> str:
    name = name or f"replay-{datetime.now(timezone.utc):%Y%m%d-%H%M}"
    conn = connect(firm.db_path)
    rules = load_rules()
    d = Drafter(firm, conn, OpenAICompatible(log=log), rules)
    d.run = name
    t = d.t
    picks = sample(conn, per_slice, slices, seed)
    rows = []
    for i, r in enumerate(picks.itertuples(), 1):
        mail = Incoming(
            r.customer_msg_id, r.thread_id, r.customer_id,
            conn.execute("SELECT text FROM messages WHERE id = ?", (r.customer_msg_id,)).fetchone()[0],
            datetime.fromisoformat(r.asked_at),
            _thread(conn, r.thread_id, r.asked_at, firm.brand_handle),
        )
        pool = int(conn.execute(
            "SELECT COUNT(*) FROM pairs WHERE is_deflection = 0 AND retired = 0 AND asked_at < ?", (r.asked_at,)
        ).fetchone()[0])
        res = d.handle(mail)
        body = draft_body(res.draft) if res.draft else None
        ref_sim = Indel.normalized_similarity(body, r.answer) if body else None
        # What reusing the closest answer word for word would have scored, for comparison.
        top = next((s for s in res.sources if s.get("type") == "past_answer"), None)
        raw_sim = None
        if top and res.route == "reuse":
            a = conn.execute("SELECT answer FROM pairs WHERE id = ?", (top["pair_id"],)).fetchone()[0]
            raw_sim = Indel.normalized_similarity(a, r.answer)
        real_commit = [m.rule for m in check_outgoing(r.answer, rules) if m.action == "placeholder"]
        rows.append(
            {
                "slice": r.slice, "asked_at": r.asked_at, "pool": pool, "route": res.route,
                "confidence": res.confidence, "best_sim": res.best_sim, "ref_sim": ref_sim, "raw_reuse_sim": raw_sim,
                "actual_was_deflection": bool(r.is_deflection),
                "actual_reply_commits": ",".join(real_commit),
                "placeholders_in_draft": ",".join(m.rule for m in res.matches if m.action == "placeholder"),
                "question": r.question[:200], "draft": body[:300] if body else None,
                "actual": r.answer[:300], "reason": res.reason,
            }
        )
        log(f"[replay] {i}/{len(picks)} {res.route:8s} sim={res.best_sim or 0:.3f} ref={'' if ref_sim is None else f'{ref_sim:.2f}'}")
        time.sleep(1.0)  # stay well inside the free tier's per-minute limits
    df = pd.DataFrame(rows)
    out_csv = firm.data_dir / f"{name}.csv"
    df.to_csv(out_csv, index=False)
    report = _report(firm, name, df, t, measures(conn, run=name))
    conn.close()
    path = ROOT / "reports" / f"replay_{firm.key}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(report, encoding="utf-8")
    log(report)
    return report


def _report(firm: Firm, name: str, df: pd.DataFrame, t: Thresholds, m: dict) -> str:
    def pct(x):
        return f"{100 * x:.0f}%"

    routes = df.route.value_counts()
    drafted = df[df.route.isin(["reuse", "docs"])]
    by_slice = df.groupby("slice").agg(
        pool=("pool", "median"),
        mails=("route", "size"),
        drafted=("route", lambda s: s.isin(["reuse", "docs"]).mean()),
        ref_sim=("ref_sim", "mean"),
        raw=("raw_reuse_sim", "mean"),
    )
    lines = [
        f"# Replay: {firm.name}",
        "",
        f"Run `{name}`, {len(df)} customer messages sampled across {df.slice.nunique()} time slices.",
        f"Thresholds: reuse {t.reuse}, docs {t.docs}, high {t.high}, medium {t.medium}"
        + (" (**provisional**: set by eye from the step 1 report, not yet from hand labels)" if t.provisional else ""),
        "",
        "**Read this first.** Similarity here is between the draft and the reply the firm actually sent. It is a",
        "reference comparison, not how much a human edited the draft: nobody edits anything in a replay.",
        "",
        "## Where mail went",
        "",
        "| route | mails | share |",
        "|---|---:|---:|",
    ]
    lines += [f"| {k} | {v} | {pct(v / len(df))} |" for k, v in routes.items()]
    lines += [
        "",
        f"Drafted: {pct(len(drafted) / len(df))}. Of the messages the firm itself only deflected (\"DM us\"): "
        f"{int(df[df.actual_was_deflection].route.isin(['reuse', 'docs']).sum())} of {int(df.actual_was_deflection.sum())} got a draft.",
        "",
        "## Does a bigger pool help? (the replay curve)",
        "",
        "| slice | pool size (median) | mails | drafted | draft vs actual reply | closest answer reused word for word |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for s, r in by_slice.iterrows():
        lines.append(
            f"| {s} | {int(r.pool):,} | {int(r.mails)} | {pct(r.drafted)} | "
            f"{'' if pd.isna(r.ref_sim) else f'{r.ref_sim:.2f}'} | {'' if pd.isna(r.raw) else f'{r.raw:.2f}'} |"
        )
    commits = df[df.actual_reply_commits != ""]
    lines += [
        "",
        "## Commitments",
        "",
        f"Actual replies in this sample that commit beyond authority by the rules: {len(commits)}.",
        f"Drafts that had a commitment replaced by a placeholder: {int((df.placeholders_in_draft != '').sum())}.",
        "",
        "## Examples",
        "",
    ]
    for _, r in df[df.route == "reuse"].head(6).iterrows():
        lines += [
            f"- **Customer:** {r.question}",
            f"  - **Draft** ({r.confidence}, sim {r.best_sim:.2f}): {r.draft}",
            f"  - **Actually sent:** {r.actual}",
        ]
    for _, r in df[df.route == "refused"].head(4).iterrows():
        lines += [f"- **Refused:** {r.question}", f"  - {r.reason}"]
    lines += ["", "Raw rows: `data/" + firm.key + f"/{name}.csv` (not committed: contains corpus text).", ""]
    lines += ["```", json.dumps(m, indent=1, default=str), "```"]
    return "\n".join(lines)

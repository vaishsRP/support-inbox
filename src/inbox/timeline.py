"""Step 9 check: run spike detection across the whole corpus timeline.

Every customer message (including ones the firm only deflected: they are still
incoming mail) gets its nearest category. Detection then steps through time
window by window, exactly as it would run live, and the report lists what it
would have raised and the messages behind each spike, so a person can judge
whether each one was a real incident.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

from .categories import SpikeConfig, assign, complaints, detect_spikes, group_incidents, load
from .config import ROOT, Firm
from .embed import cached
from .store import connect


def events(firm: Firm, answered_only: bool = False) -> tuple[pd.DataFrame, dict]:
    conn = connect(firm.db_path)
    extra = " AND is_deflection = 0" if answered_only else ""
    df = pd.read_sql_query(
        "SELECT id, customer_id, asked_at, question FROM pairs WHERE source='corpus' AND asked_at >= '2017-10-01'"
        + extra + " ORDER BY asked_at",
        conn,
    )
    conn.close()
    cats, centroids = load(firm)
    vecs = cached(firm.data_dir / "vec_questions.npz", df.id.tolist(), df.question.tolist())
    df["category"] = assign(vecs, centroids)
    df["ts"] = pd.to_datetime(df.asked_at, utc=True)
    return df, {c.id: c.name for c in cats}


def spike_report(firm: Firm, window_hours: int = 6, answered_only: bool = False) -> str:
    df, names = events(firm, answered_only)
    cfg = SpikeConfig(window_hours=window_hours)
    start = df.ts.min().ceil("h") + timedelta(days=cfg.baseline_days)
    end = df.ts.max()
    step = timedelta(hours=window_hours)
    open_: dict[int, dict] = {}
    found = []
    t = start
    while t <= end:
        live = {s["category"]: s for s in detect_spikes(df, t.to_pydatetime(), cfg)}
        for cat, s in live.items():
            if cat not in open_:
                open_[cat] = {**s, "opened": t, "peak": s["customers"]}
            else:
                open_[cat]["peak"] = max(open_[cat]["peak"], s["customers"])
        for cat in list(open_):
            if cat not in live:
                sp = open_.pop(cat)
                sp["closed"] = t
                found.append(sp)
        t += step
    found += [{**s, "closed": None} for s in open_.values()]
    incidents = group_incidents(found, step)

    lines = [
        f"# Spikes over the corpus timeline: {firm.name}",
        "",
        f"{len(df):,} customer messages from {df.ts.min():%d %b} to {df.ts.max():%d %b %Y}, stepping through in "
        f"{window_hours}-hour windows. A spike needs at least {cfg.min_customers} distinct customers, {cfg.ratio:g}x the "
        f"category's normal rate for a window and {cfg.sigmas:g} standard deviations above it, measured over the previous "
        f"{cfg.baseline_days} days. Category names are the suggested ones until renamed by hand."
        + (" Only messages the firm answered (not deflected) are counted, to save embedding time; "
           "spikes are therefore undercounted." if answered_only else ""),
        "",
        f"**{len(found)} category spikes, grouped into {len(incidents)} incidents** (spikes overlapping in time are "
        "one incident, and one action-list row).",
        "",
        "## Incidents",
        "",
        "| opened | closed | topics | peak customers in one topic |",
        "|---|---|---|---:|",
    ]
    for inc in incidents:
        topics = ", ".join(dict.fromkeys(str(names.get(c, c)) for c in inc["categories"]))
        lines.append(f"| {inc['opened']:%a %d %b %H:%M} | {inc['closed']:%a %d %b %H:%M} | {topics} | {inc['peak']} |")
    lines += [
        "",
        "## Every category spike",
        "",
        "| opened | closed | category | peak customers in a window | normal |",
        "|---|---|---|---:|---:|",
    ]
    for s in sorted(found, key=lambda x: x["opened"]):
        lines.append(
            f"| {s['opened']:%a %d %b %H:%M} | {'' if s['closed'] is None else format(s['closed'], '%a %d %b %H:%M')} | "
            f"{names.get(s['category'], s['category'])} | {s['peak']} | {s['normal']} |"
        )
    lines += ["", "## What was behind each spike", ""]
    for s in sorted(found, key=lambda x: -x["peak"])[:8]:
        w = df[(df.category == s["category"]) & (df.ts > s["opened"] - step) & (df.ts <= s["opened"])]
        lines.append(f"### {names.get(s['category'], s['category'])}, {s['opened']:%a %d %b %H:%M}")
        for q in w.drop_duplicates("customer_id").question.head(6):
            lines.append(f"- {q[:160]}")
        lines.append("")

    comp = complaints(df, names, df.ts.max().to_pydatetime(), days=7)
    lines += [
        "## Frequent complaints, last 7 days of the corpus",
        "",
        "| category | customers | previous 7 days | change |",
        "|---|---:|---:|---:|",
    ]
    for cat, r in comp.head(12).iterrows():
        lines.append(f"| {r['name']} | {r.customers} | {r.previous} | {r.change:+d} |")
    text = "\n".join(lines) + "\n"
    out = ROOT / "reports" / f"step9_spikes_{firm.key}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text)
    return text

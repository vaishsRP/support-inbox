"""Step 1: how repetitive is the corpus, really?

The whole answer-once premise rests on this. For every customer question, look
only at questions asked *earlier* (no peeking at the future), take the closest
one, and ask two things:

1. How close is the question? (embedding cosine similarity)
2. Had we reused that earlier question's approved answer, how close would it be
   to the answer the firm actually sent? (character-level similarity, i.e. one
   minus normalised edit distance, and embedding similarity)

If answer closeness rises with question closeness, reuse works. If near-identical
questions got unrelated answers, the premise fails and the project is an ordinary
retrieval mailbox. The random-earlier-answer baseline shows what "close" means.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
from rapidfuzz.distance import Indel

from .config import ROOT, Firm
from .embed import cached
from .store import connect

CHUNK = 1000
SEED = 7


def _nearest_earlier(vecs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For row i (rows sorted by time) the best j < i and its cosine similarity."""
    n = len(vecs)
    best_j = np.full(n, -1, dtype=np.int64)
    best_s = np.full(n, np.nan, dtype=np.float32)
    for a in range(1, n, CHUNK):
        b = min(n, a + CHUNK)
        sims = vecs[a:b] @ vecs[:b].T
        rows = np.arange(a, b)[:, None]
        sims[np.arange(b)[None, :] >= rows] = -np.inf
        j = sims.argmax(axis=1)
        best_j[a:b] = j
        best_s[a:b] = sims[np.arange(b - a), j]
    return best_j, best_s


def _pct(x) -> str:
    return f"{100 * float(x):.0f}%"


def load_pairs(firm: Firm, include_deflections: bool = False) -> pd.DataFrame:
    conn = connect(firm.db_path)
    where = "" if include_deflections else "WHERE is_deflection = 0"
    df = pd.read_sql_query(
        f"SELECT id, question, answer, asked_at, is_followup, is_deflection FROM pairs {where} "
        "ORDER BY asked_at, id",
        conn,
    )
    conn.close()
    return df.reset_index(drop=True)


def analyse(firm: Firm, include_deflections: bool) -> dict:
    df = load_pairs(firm, include_deflections)
    tag = "all" if include_deflections else "answers"
    qv = cached(firm.data_dir / "vec_questions.npz", df.id.tolist(), df.question.tolist())
    av = cached(firm.data_dir / "vec_answers.npz", df.id.tolist(), df.answer.tolist())
    j, s = _nearest_earlier(qv)
    df["nn"] = j
    df["q_sim"] = s

    rng = np.random.default_rng(SEED)
    ok = df.index[df.nn >= 0]
    rand = np.array([rng.integers(0, i) for i in ok])
    reuse = df.loc[ok, "nn"].to_numpy()

    def lex(a_idx, b_idx):
        A, B = df.answer.to_numpy(), df.answer.to_numpy()
        return np.array([Indel.normalized_similarity(A[x], B[y]) for x, y in zip(a_idx, b_idx)])

    df.loc[ok, "a_lex"] = lex(ok, reuse)
    df.loc[ok, "a_emb"] = (av[ok] * av[reuse]).sum(1)
    df.loc[ok, "base_lex"] = lex(ok, rand)
    df.loc[ok, "base_emb"] = (av[ok] * av[rand]).sum(1)
    df["tag"] = tag
    return {"df": df.loc[ok], "tag": tag, "n_total": len(df)}


def _bucket_table(d: pd.DataFrame) -> str:
    bins = [-1, 0.80, 0.85, 0.88, 0.90, 0.92, 0.94, 0.96, 1.01]
    labels = ["<0.80", "0.80-0.85", "0.85-0.88", "0.88-0.90", "0.90-0.92", "0.92-0.94", "0.94-0.96", ">=0.96"]
    d = d.assign(bucket=pd.cut(d.q_sim, bins=bins, labels=labels, right=False))
    g = d.groupby("bucket", observed=False).agg(
        n=("q_sim", "size"),
        answer_char_sim=("a_lex", "mean"),
        answer_emb_sim=("a_emb", "mean"),
        near_copy=("a_lex", lambda x: (x >= 0.8).mean() if len(x) else np.nan),
    )
    lines = [
        "| closest earlier question similarity | questions | share | reused answer, char similarity | reused answer, meaning similarity | reused answer is a near copy (>=0.8) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for b, r in g.iterrows():
        if r.n == 0:
            continue
        lines.append(
            f"| {b} | {int(r.n):,} | {_pct(r.n / len(d))} | {r.answer_char_sim:.2f} | {r.answer_emb_sim:.3f} | {_pct(r.near_copy)} |"
        )
    return "\n".join(lines)


def _examples(d: pd.DataFrame, full: pd.DataFrame, k: int = 4) -> str:
    out = []
    picks = d[(d.q_sim >= 0.94) & (d.is_followup == 0)].sample(min(k, len(d)), random_state=SEED)
    for _, r in picks.iterrows():
        nn = full.loc[int(r.nn)]
        out.append(
            f"- **Q** {r.question[:180]}\n  - **Earlier Q** (sim {r.q_sim:.2f}): {nn.question[:180]}\n"
            f"  - **Earlier answer, reused**: {nn.answer[:220]}\n  - **Actual answer**: {r.answer[:220]}\n"
            f"  - char similarity {r.a_lex:.2f}"
        )
    return "\n".join(out)


def run(firm: Firm) -> str:
    conn = connect(firm.db_path)
    total = conn.execute("SELECT COUNT(*) FROM pairs").fetchone()[0]
    defl = conn.execute("SELECT COUNT(*) FROM pairs WHERE is_deflection = 1").fetchone()[0]
    span = conn.execute("SELECT MIN(asked_at), MAX(asked_at) FROM pairs").fetchone()
    conn.close()

    res = analyse(firm, include_deflections=False)
    d = res["df"]
    full = load_pairs(firm, include_deflections=False)
    first = d[d.is_followup == 0]

    def summary(x: pd.DataFrame) -> str:
        return (
            f"| {len(x):,} | {x.q_sim.median():.3f} | {_pct((x.q_sim >= 0.92).mean())} | "
            f"{x.a_lex.mean():.2f} | {x.base_lex.mean():.2f} | {x.a_emb.mean():.3f} | {x.base_emb.mean():.3f} | "
            f"{_pct((x.a_lex >= 0.8).mean())} | {_pct((x.base_lex >= 0.8).mean())} |"
        )

    head = (
        "| set | questions | median closest-question sim | share with a very close earlier question (>=0.92) "
        "| reused answer char sim | random answer char sim | reused answer meaning sim | random answer meaning sim "
        "| reused answer is a near copy | random answer is a near copy |\n|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
    )

    res_all = analyse(firm, include_deflections=True)
    da = res_all["df"]

    report = f"""# Step 1: how repetitive is {firm.name}?

Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by `python -m inbox --firm {firm.key} explore`.

Corpus: {total:,} customer message / firm reply pairs, asked {span[0][:10]} to {span[1][:10]}.
Deflections ("DM us", "contact our team"): {defl:,} ({_pct(defl / total)}). They are
excluded below unless marked, because they repeat endlessly but answer nothing.

Method: for each question, only questions asked earlier are searchable. Take the
closest by meaning (embedding cosine, `multilingual-e5-small`). Then compare that
earlier question's approved answer with the answer the firm actually sent:
character similarity (1 minus normalised edit distance) and meaning similarity.
The baseline is a random earlier answer. A "near copy" is character similarity of
0.8 or more, meaning a light edit would have turned the reused answer into the
real one.

## Headline

| set | questions | median closest-question sim | share >=0.92 | reused char sim | random char sim | reused meaning sim | random meaning sim | reused near copy | random near copy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
{summary(d).replace("| ", "| real answers, all messages | ", 1)}
{summary(first).replace("| ", "| real answers, first messages only | ", 1)}
{summary(da).replace("| ", "| including deflections (for contrast) | ", 1)}

## Does a closer question mean a more reusable answer?

Real answers only. If reuse works, the right-hand columns rise going down the table.

{_bucket_table(d)}

## Examples: very close earlier question (first messages only, sim >= 0.94)

{_examples(d, full)}
"""
    out = ROOT / "reports" / f"step1_{firm.key}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"[explore] written to {out.relative_to(ROOT)}")
    return report

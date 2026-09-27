"""Retrieval over the pool of approved answers (and, from step 10, documents and notes).

Everything is brute-force cosine similarity over normalised vectors in memory. A
firm's pool is tens of thousands of answers at most; a vector database would be
one more service to run for no gain.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from .config import Firm
from .embed import cached, encode
from .store import connect


@dataclass
class Hit:
    pair_id: int
    sim: float
    question: str
    answer: str
    asked_at: str
    thread_id: str


class AnswerIndex:
    """The approved-answer pool for one firm."""

    def __init__(self, firm: Firm):
        self.firm = firm
        conn = connect(firm.db_path)
        self.df = pd.read_sql_query(
            "SELECT id, thread_id, question, answer, asked_at, owner FROM pairs "
            "WHERE is_deflection = 0 AND retired = 0 ORDER BY asked_at, id",
            conn,
        )
        conn.close()
        self.vecs = cached(firm.data_dir / "vec_questions.npz", self.df.id.tolist(), self.df.question.tolist())
        self.asked = self.df.asked_at.to_numpy()

    def add(self, pair_id: int) -> None:
        """A reply that was just sent joins the pool straight away: the answer-once loop."""
        conn = connect(self.firm.db_path)
        row = conn.execute(
            "SELECT id, thread_id, question, answer, asked_at, owner FROM pairs WHERE id = ?", (pair_id,)
        ).fetchone()
        conn.close()
        if row is None:
            return
        keep = self.df.id.to_numpy() != pair_id           # a re-sent reply replaces the old one
        self.df = pd.concat([self.df[keep], pd.DataFrame([dict(row)])], ignore_index=True)
        self.vecs = np.vstack([self.vecs[keep], encode([row["question"]])])
        self.asked = self.df.asked_at.to_numpy()

    def __len__(self) -> int:
        return len(self.df)

    def search(
        self,
        text: str | None = None,
        vec: np.ndarray | None = None,
        k: int = 3,
        before: str | datetime | None = None,
        exclude_thread: str | None = None,
        exclude_answers: list[str] | None = None,
        owner: str | None = None,
    ) -> list[Hit]:
        """Top-k closest past questions.

        before           only answers that existed at that time (replay; no future leakage)
        exclude_thread   skip this thread's own pairs
        exclude_answers  skip answers already sent in this thread ("still not working")
        """
        if vec is None:
            vec = encode([text])[0]
        sims = self.vecs @ vec
        # Team-wide answers, plus (public demo only) this visitor's own sent replies.
        owners = self.df.owner.to_numpy() if "owner" in self.df else np.full(len(sims), None)
        mask = np.array([o is None or (isinstance(o, float) and np.isnan(o)) or o == owner for o in owners])
        if before is not None:
            cutoff = before.isoformat() if isinstance(before, datetime) else before
            mask &= self.asked < cutoff
        if exclude_thread is not None:
            mask &= self.df.thread_id.to_numpy() != str(exclude_thread)
        sims = np.where(mask, sims, -np.inf)
        order = np.argsort(-sims)
        hits, seen = [], set(exclude_answers or [])
        for i in order:
            if not np.isfinite(sims[i]) or len(hits) >= k:
                break
            row = self.df.iloc[i]
            if row.answer in seen:
                continue
            seen.add(row.answer)
            hits.append(Hit(int(row.id), float(sims[i]), row.question, row.answer, row.asked_at, row.thread_id))
        return hits


def eyeball(firm: Firm, n: int = 12, seed: int = 5) -> str:
    """Step 2 deliverable: sample real questions, show the three closest earlier threads."""
    from .config import ROOT

    idx = AnswerIndex(firm)
    rng = np.random.default_rng(seed)
    # Sample from the second half of the period so there is a pool to search.
    start = len(idx) // 2
    picks = rng.choice(np.arange(start, len(idx)), size=n, replace=False)
    lines = [
        f"# Step 2: closest past threads, {firm.name}",
        "",
        "Each block is a real customer message from the second half of the corpus. Below it are the",
        "three closest questions asked *before* it, with the answer the firm gave then. Judge by eye:",
        "would one of those answers, lightly adapted, have answered the new message?",
        "",
    ]
    for i in sorted(picks):
        row = idx.df.iloc[i]
        hits = idx.search(vec=idx.vecs[i], k=3, before=row.asked_at, exclude_thread=row.thread_id)
        lines.append(f"## {row.question[:240]}")
        lines.append(f"*Actual answer:* {row.answer[:240]}")
        lines.append("")
        for h in hits:
            lines.append(f"- **{h.sim:.3f}** Q: {h.question[:200]}")
            lines.append(f"  - A: {h.answer[:220]}")
        lines.append("")
    out = ROOT / "reports" / f"step2_{firm.key}.md"
    out.parent.mkdir(exist_ok=True)
    text = "\n".join(lines)
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"[retrieve] written to {out.relative_to(ROOT)}")
    return text

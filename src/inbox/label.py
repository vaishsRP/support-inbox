"""Step 3: hand-label pairs, then set the thresholds from the labels.

  python -m inbox --firm americanair label       # label in the terminal; resumable
  python -m inbox --firm americanair calibrate   # thresholds from the tune split, scored on the test split

Each item is a real customer message and the approved answer to the closest
*earlier* question. The only question: would that answer, lightly adapted, have
answered this message? The firm's actual reply is hidden so it does not anchor
the judgement.

Items are sampled evenly across the similarity range, so the threshold is set on
the part of the range where the decision is actually hard. A fixed hash of each
item decides whether it lands in the tune split (about two thirds) or the test
split, which is looked at only after the thresholds are fixed.
"""

from __future__ import annotations

import csv
import hashlib
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml

from .config import FIRMS_DIR, ROOT, Firm
from .retrieve import AnswerIndex

N_ITEMS = 150
BINS = [0.80, 0.84, 0.87, 0.89, 0.91, 0.93, 0.95, 0.97, 1.001]
FIELDS = ["item", "split", "sim", "pair_id", "earlier_pair_id", "question", "earlier_question", "earlier_answer", "label", "labelled_at"]


def labels_path(firm: Firm) -> Path:
    return ROOT / "labels" / f"{firm.key}_pairs.csv"


def split_of(item: str) -> str:
    return "test" if int(hashlib.sha1(item.encode()).hexdigest(), 16) % 3 == 0 else "tune"


def build_items(firm: Firm, n: int = N_ITEMS, seed: int = 11) -> list[dict]:
    idx = AnswerIndex(firm)
    rng = np.random.default_rng(seed)
    first = idx.df.index[(idx.df.index >= len(idx) // 4)].to_numpy()
    rng.shuffle(first)
    per_bin = int(np.ceil(n / (len(BINS) - 1)))
    buckets: dict[int, list[dict]] = {b: [] for b in range(len(BINS) - 1)}
    for i in first:
        if all(len(v) >= per_bin for v in buckets.values()):
            break
        row = idx.df.iloc[i]
        hits = idx.search(vec=idx.vecs[i], k=1, before=row.asked_at, exclude_thread=row.thread_id)
        if not hits:
            continue
        h = hits[0]
        b = int(np.searchsorted(BINS, h.sim, side="right")) - 1
        if 0 <= b < len(BINS) - 1 and len(buckets[b]) < per_bin:
            item = f"{row.id}-{h.pair_id}"
            buckets[b].append(
                {
                    "item": item,
                    "split": split_of(item),
                    "sim": f"{h.sim:.4f}",
                    "pair_id": int(row.id),
                    "earlier_pair_id": h.pair_id,
                    "question": row.question,
                    "earlier_question": h.question,
                    "earlier_answer": h.answer,
                    "label": "",
                    "labelled_at": "",
                }
            )
    items = [x for b in buckets.values() for x in b]
    rng.shuffle(items)
    return items[:n]


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def prepare(firm: Firm) -> list[dict]:
    path = labels_path(firm)
    rows = _read(path)
    if not rows:
        print("[label] sampling items across the similarity range (first run only)...")
        rows = build_items(firm)
        _write(path, rows)
    return rows


def run_labelling(firm: Firm) -> None:
    rows = prepare(firm)
    path = labels_path(firm)
    todo = [r for r in rows if not r["label"]]
    done = len(rows) - len(todo)
    wrap = lambda s: textwrap.fill(s, 88, initial_indent="    ", subsequent_indent="    ")  # noqa: E731
    print(
        f"\n{done} of {len(rows)} labelled. For each item: would the EARLIER ANSWER, lightly adapted,\n"
        "have answered the NEW MESSAGE?   y = yes   n = no   s = skip   u = undo last   q = save and quit\n"
    )
    history = []
    i = 0
    while i < len(todo):
        r = todo[i]
        print("-" * 92)
        print(f"[{done + i + 1}/{len(rows)}]")
        print("  NEW MESSAGE:")
        print(wrap(r["question"]))
        print("  EARLIER QUESTION:")
        print(wrap(r["earlier_question"]))
        print("  EARLIER ANSWER:")
        print(wrap(r["earlier_answer"]))
        key = input("  reusable? [y/n/s/u/q] ").strip().lower()[:1]
        if key == "q":
            break
        if key == "u" and history:
            prev = history.pop()
            prev["label"], prev["labelled_at"] = "", ""
            i -= 1
            _write(path, rows)
            continue
        if key in ("y", "n", "s"):
            r["label"] = {"y": "reusable", "n": "not_reusable", "s": "skip"}[key]
            r["labelled_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            history.append(r)
            _write(path, rows)
            i += 1
    left = sum(1 for r in rows if not r["label"])
    print(f"\nSaved to {path.relative_to(ROOT)}. {len(rows) - left} labelled, {left} to go.")


# ---- Calibration -------------------------------------------------------------------


def _precision_curve(sims: np.ndarray, ys: np.ndarray):
    order = np.argsort(-sims)
    s, y = sims[order], ys[order]
    tp = np.cumsum(y)
    n = np.arange(1, len(y) + 1)
    return s, tp / n, tp / max(1, y.sum())


def pick_threshold(sims: np.ndarray, ys: np.ndarray, target: float) -> float | None:
    """The lowest similarity at which everything above it is at least `target` reusable."""
    s, prec, _ = _precision_curve(sims, ys)
    ok = np.where(prec >= target)[0]
    if not len(ok):
        return None
    # the deepest point in the ranking that still meets the precision target
    return float(s[ok.max()])


def calibrate(firm: Firm, reuse_target: float = 0.85, high_target: float = 0.95) -> str:
    rows = [r for r in _read(labels_path(firm)) if r["label"] in ("reusable", "not_reusable")]
    tune = [r for r in rows if r["split"] == "tune"]
    test = [r for r in rows if r["split"] == "test"]
    if len(tune) < 40:
        raise SystemExit(f"Only {len(tune)} tune labels so far; label more first (about 100 needed).")
    sims = np.array([float(r["sim"]) for r in tune])
    ys = np.array([r["label"] == "reusable" for r in tune], dtype=float)
    reuse = pick_threshold(sims, ys, reuse_target)
    high = pick_threshold(sims, ys, high_target)
    if reuse is None:
        raise SystemExit("No threshold reaches the precision target: reuse does not work on this corpus.")
    high = max(high or reuse, reuse)
    medium = (reuse + high) / 2

    def score(rs, t):
        s = np.array([float(r["sim"]) for r in rs])
        y = np.array([r["label"] == "reusable" for r in rs])
        above = s >= t
        prec = y[above].mean() if above.any() else float("nan")
        rec = (y & above).sum() / max(1, y.sum())
        return above.sum(), prec, rec

    n_tune, p_tune, r_tune = score(tune, reuse)
    n_test, p_test, r_test = score(test, reuse) if test else (0, float("nan"), float("nan"))

    path = FIRMS_DIR / f"{firm.key}.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["thresholds"] = {
        "reuse": round(reuse, 4),
        "docs": raw.get("thresholds", {}).get("docs", 0.86),
        "high": round(high, 4),
        "medium": round(medium, 4),
        "provisional": False,
    }
    text = path.read_text(encoding="utf-8")
    head = "".join(l for l in text.splitlines(keepends=True) if l.startswith("#"))
    body = {k: v for k, v in raw.items()}
    path.write_text(head + "\n" + yaml.safe_dump(body, sort_keys=False), encoding="utf-8")

    report = f"""# Step 3: thresholds for {firm.name}

From {len(tune)} hand-labelled tune pairs; scored once on {len(test)} held-back test pairs.

| | value |
|---|---|
| reuse threshold (>= {reuse_target:.0%} of answers above it reusable, on tune) | {reuse:.4f} |
| high confidence from | {high:.4f} |
| medium confidence from | {medium:.4f} |
| tune: pairs above threshold / precision / recall | {n_tune} / {p_tune:.2f} / {r_tune:.2f} |
| **test: pairs above threshold / precision / recall** | **{n_test} / {p_test:.2f} / {r_test:.2f}** |

Share labelled reusable, all labels: {np.mean([r['label'] == 'reusable' for r in rows]):.2f}.
The test numbers are the honest ones; the tune numbers were used to pick the threshold.
"""
    out = ROOT / "reports" / f"step3_{firm.key}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(report)
    return report

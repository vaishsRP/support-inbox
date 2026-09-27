"""Command line entry point: python -m inbox <command> --firm <key>"""

from __future__ import annotations

import argparse
import sys

from .config import load_firm


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="inbox")
    ap.add_argument("--firm", default="americanair")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("import", help="load the development corpus into the firm's database")
    sub.add_parser("explore", help="step 1: report how repetitive the corpus is")
    p = sub.add_parser("retrieve", help="step 2: closest past threads for a message")
    p.add_argument("text", nargs="?", help="a new message; omit to write the eyeball report")
    p.add_argument("-k", type=int, default=3)
    sub.add_parser("label", help="step 3: hand-label pairs in the terminal (resumable)")
    sub.add_parser("calibrate", help="step 3: set thresholds from the labels")
    args = ap.parse_args(argv)
    firm = load_firm(args.firm)

    if args.cmd == "import":
        from .corpus import import_twcs

        import_twcs(firm)
    elif args.cmd == "explore":
        from .explore import run

        run(firm)
    elif args.cmd == "retrieve":
        from .retrieve import AnswerIndex, eyeball

        if not args.text:
            eyeball(firm)
        else:
            for h in AnswerIndex(firm).search(args.text, k=args.k):
                print(f"{h.sim:.3f}  Q: {h.question}\n       A: {h.answer}\n")
    elif args.cmd == "label":
        from .label import run_labelling

        run_labelling(firm)
    elif args.cmd == "calibrate":
        from .label import calibrate

        calibrate(firm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

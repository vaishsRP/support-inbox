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
    p = sub.add_parser("explore", help="step 1: report how repetitive the corpus is")
    p.add_argument("--skip-deflections", action="store_true", help="skip the slow with-deflections contrast row")
    p = sub.add_parser("retrieve", help="step 2: closest past threads for a message")
    p.add_argument("text", nargs="?", help="a new message; omit to write the eyeball report")
    p.add_argument("-k", type=int, default=3)
    sub.add_parser("label", help="step 3: hand-label pairs in the terminal (resumable)")
    sub.add_parser("calibrate", help="step 3: set thresholds from the labels")
    p = sub.add_parser("replay", help="replay the corpus through the drafter (uses the language model)")
    p.add_argument("--per-slice", type=int, default=25)
    p.add_argument("--slices", type=int, default=3)
    p = sub.add_parser("categorize", help="step 9: cluster questions into suggested categories")
    p.add_argument("-k", type=int, default=24)
    p = sub.add_parser("spikes", help="step 9: replay spike detection over the corpus timeline")
    p.add_argument("--window-hours", type=int, default=6)
    p.add_argument("--answered-only", action="store_true", help="count only answered messages (faster)")
    sub.add_parser("digest", help="print today's digest")
    sub.add_parser("scenarios", help="realistic end-to-end scenarios with the real model")
    sub.add_parser("gmail-auth", help="sign in to the Gmail account once (opens a browser)")
    p = sub.add_parser("gmail-once", help="one pass over the Gmail inbox and sent folder")
    p.add_argument("--catch-up-hours", type=int, default=None, help="also handle inbox mail from the last N hours")
    p = sub.add_parser("gmail-import", help="read the mailbox's past correspondence into the knowledge base")
    p.add_argument("--months", type=int, default=12)
    p = sub.add_parser("gmail-run", help="watch the Gmail inbox; Ctrl+C to stop")
    p.add_argument("--every", type=int, default=90, help="seconds between passes")
    args = ap.parse_args(argv)
    firm = load_firm(args.firm)

    if args.cmd == "import":
        if firm.corpus_kind == "helpdesk":
            from .helpdesk import import_helpdesk

            import_helpdesk(firm)
        else:
            from .corpus import import_twcs

            import_twcs(firm)
    elif args.cmd == "explore":
        from .explore import run

        run(firm, with_deflections=not args.skip_deflections)
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
    elif args.cmd == "replay":
        from .replay import run as replay

        replay(firm, per_slice=args.per_slice, slices=args.slices)
    elif args.cmd == "categorize":
        from .categories import cluster

        cluster(firm, k=args.k)
    elif args.cmd == "spikes":
        from .timeline import spike_report

        spike_report(firm, window_hours=args.window_hours, answered_only=args.answered_only)
    elif args.cmd == "digest":
        from . import actions
        from .store import connect

        print(actions.digest(connect(firm.db_path), firm_name=firm.name))
    elif args.cmd == "scenarios":
        from .scenarios import run as run_scenarios

        print(run_scenarios(firm))
    elif args.cmd.startswith("gmail"):
        from .gmail import Mailbox, poll_once, run_forever

        if args.cmd == "gmail-auth":
            mb = Mailbox.connect(firm, interactive=True)
            mb.ensure_labels()
            print(f"Signed in as {mb.me}. Labels are ready. Start with: python -m inbox --firm {firm.key} gmail-run")
            return 0
        from .draft import Drafter
        from .llm import OpenAICompatible
        from .rules import load_rules
        from .store import connect

        rules = load_rules()
        drafter = Drafter(firm, connect(firm.db_path), OpenAICompatible(), rules)
        if args.cmd == "gmail-import":
            from .history import import_mailbox

            mb = Mailbox.connect(firm)
            import_mailbox(mb, drafter.conn, firm, months=args.months, pause=0.05)
            return 0
        if args.cmd == "gmail-once":
            mb = Mailbox.connect(firm)
            mb.ensure_labels()
            print(poll_once(mb, drafter, drafter.conn, firm, rules, catch_up_hours=args.catch_up_hours))
        else:
            run_forever(firm, drafter, rules, every=args.every)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

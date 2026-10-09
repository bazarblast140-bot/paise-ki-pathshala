"""python -m realchart run --kind news|close|ta|recap [--dry-run]."""
from __future__ import annotations

import argparse
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(prog="realchart")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run")
    run_p.add_argument("--kind", required=True, choices=("news", "close", "ta", "recap"))
    run_p.add_argument("--dry-run", action="store_true")
    run_p.add_argument("--out", default="run")
    args = parser.parse_args(argv)
    from realchart.build import run
    code = run(args.kind, args.out, dry_run=args.dry_run)
    return int(code or 0)


if __name__ == "__main__":
    sys.exit(main())

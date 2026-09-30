#!/usr/bin/env python3
"""One-shot: reassemble board_short.py from committed base64 chunks."""
from pathlib import Path
import base64
root = Path(__file__).resolve().parent
parts = sorted((root / "board_short_chunks").glob("c*.b64"))
if not parts:
    raise SystemExit("no chunks")
data = base64.b64decode("".join(p.read_text().strip() for p in parts))
out = root / "board_short.py"
out.write_bytes(data)
print(f"wrote {out} ({len(data)} bytes)")

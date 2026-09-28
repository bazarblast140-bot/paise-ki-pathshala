"""Chart Board Short — loader. Body split across board_short_a/b for deploy size."""
from pathlib import Path
_dir = Path(__file__).resolve().parent
_code = (_dir / "board_short_a.py").read_text(encoding="utf-8") + (_dir / "board_short_b.py").read_text(encoding="utf-8")
exec(compile(_code, str(_dir / "board_short.py"), "exec"), globals())

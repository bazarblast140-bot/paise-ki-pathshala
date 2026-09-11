"""Preflight: fail fast on setup problems BEFORE burning API credits.

Runs first in run_pipeline.py. Can also be run alone:  python check_setup.py
"""
import os, shutil, sys
from common import cfg, ROOT, ok

def main():
    problems, warnings = [], []
    c = cfg()

    # --- secrets ---
    provider = c["models"].get("llm_provider", "anthropic").lower()
    has_kimi = bool(os.environ.get("KIMI_API_KEY"))
    has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if provider == "kimi":
        if not has_kimi:
            problems.append("config.json selects Kimi, but KIMI_API_KEY is missing. Add KIMI_API_KEY in GitHub Repository Secrets.")
    elif provider == "anthropic":
        if not has_anthropic:
            problems.append("config.json selects Anthropic, but ANTHROPIC_API_KEY is missing. Add ANTHROPIC_API_KEY in GitHub Repository Secrets.")
    elif provider == "deepseek":
        # Pehle ye branch tha hi nahi: config DeepSeek chunta tha par preflight
        # Kimi/Anthropic key maangta tha — naye repo me sirf DeepSeek key ho to
        # pipeline bina wajah ruk jaati.
        if not os.environ.get("DEEPSEEK_API_KEY"):
            problems.append("config.json selects DeepSeek, but DEEPSEEK_API_KEY is missing. Add DEEPSEEK_API_KEY in GitHub Repository Secrets.")
    elif not (has_kimi or has_anthropic):
        problems.append("No LLM API key set. Set KIMI_API_KEY or ANTHROPIC_API_KEY in GitHub Repository Secrets.")

    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        if not os.environ.get(key):
            warnings.append(f"{key} not set — report will print to console instead of Telegram.")

    # --- config TODOs ---
    if str(c["channel"]["name"]).startswith("TODO"):
        warnings.append("config.json -> channel.name is still TODO.")

    # --- voice engine (graceful fallback) ---
    engine = c["voice"].get("engine", "edge").lower()
    if engine == "elevenlabs":
        if not os.environ.get("ELEVENLABS_API_KEY"):
            warnings.append("voice.engine is 'elevenlabs' but ELEVENLABS_API_KEY is not set — will fall back to free Edge TTS.")
        if str(c["voice"]["voice_id"]).startswith("TODO"):
            warnings.append("voice.engine is 'elevenlabs' but voice.voice_id is still TODO — will fall back to free Edge TTS.")
    elif engine == "edge":
        try:
            __import__("edge_tts")
        except ImportError:
            problems.append("voice.engine is 'edge' but edge-tts is not installed. Run: pip install -r requirements.txt")
    else:
        problems.append(f"config.json -> voice.engine '{engine}' unknown (use 'edge' or 'elevenlabs').")

    # --- binaries ---
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            problems.append(f"{tool} not on PATH. Windows: winget install Gyan.FFmpeg "
                            "(then open a NEW terminal). macOS: brew install ffmpeg.")

    # --- python deps ---
    for mod, pkg in (("anthropic", "anthropic"), ("requests", "requests"), ("PIL", "Pillow")):
        try:
            __import__(mod)
        except ImportError:
            problems.append(f"Python package missing: {pkg}. Run: pip install -r requirements.txt")

    # --- topics left ---
    try:
        import json
        bank = json.loads((ROOT / "topic_bank.json").read_text(encoding="utf-8"))
        left = sum(1 for t in bank["topics"] if not t["used"])
        if left == 0:
            problems.append("topic_bank.json has no unused topics left. Add more.")
        elif left <= 3:
            warnings.append(f"Only {left} unused topic(s) left in topic_bank.json.")
    except (OSError, ValueError, KeyError) as e:
        problems.append(f"topic_bank.json unreadable: {e}")

    # --- fonts (cosmetic) ---
    fonts = list((ROOT / "assets/fonts").glob("*.ttf")) if (ROOT / "assets/fonts").exists() else []
    if not fonts and not os.path.exists("C:/Windows/Fonts/segoeuib.ttf"):
        warnings.append("No TTF in assets/fonts — cards will use a fallback font.")

    for w in warnings:
        print(f"[warn] {w}")
    if problems:
        print("\n[FAIL] Preflight found blocking problems:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        sys.exit(1)

    ok(f"preflight passed{' with ' + str(len(warnings)) + ' warning(s)' if warnings else ''}")

if __name__ == "__main__":
    main()

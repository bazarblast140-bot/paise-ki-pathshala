"""Per-run credit record. Shared by every pipeline subprocess via run/credits.json.

DeepSeek calls (including retries) increment before the HTTP request so a hung
call still counts. The cap fails closed: the next call is not made. ElevenLabs
character counts are characters placed on a TTS request. The subscription
balance is whatever a free GET already returned; this module never calls it.
"""
import json
import os
from pathlib import Path

PATH = Path(__file__).resolve().parent / "run" / "credits.json"

# One Short used to retry script and review until the balance was gone.
DEFAULT_DEEPSEEK_CALLS = 4


class DeepSeekCallCap(RuntimeError):
    """The run has used its DeepSeek calls. Do not make another one."""

    def __init__(self, used, cap):
        self.used = int(used)
        self.cap = int(cap)
        super().__init__(
            f"DeepSeek call cap reached ({self.used}/{self.cap}); "
            "skipping the slot before another paid call"
        )


def call_cap():
    raw = (os.environ.get("DEEPSEEK_MAX_CALLS") or "").strip()
    if not raw:
        return DEFAULT_DEEPSEEK_CALLS
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_DEEPSEEK_CALLS


def reset():
    try:
        PATH.unlink()
    except OSError:
        pass


def _read():
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def reserve_deepseek_call():
    """Count one DeepSeek HTTP call. Raise DeepSeekCallCap before it is sent."""
    data = _read()
    cap = call_cap()
    used = _as_int(data.get("deepseek_calls")) or 0
    if used >= cap:
        raise DeepSeekCallCap(used, cap)
    data["deepseek_calls"] = used + 1
    _write(data)
    return used + 1


def _usage_pair(completion):
    usage = getattr(completion, "usage", None)
    if usage is None and isinstance(completion, dict):
        usage = completion.get("usage")
    if usage is None:
        return None, None
    if isinstance(usage, dict):
        return _as_int(usage.get("prompt_tokens")), _as_int(usage.get("completion_tokens"))
    return _as_int(getattr(usage, "prompt_tokens", None)), _as_int(getattr(usage, "completion_tokens", None))


def add_deepseek_usage(completion):
    """Add prompt/completion tokens when the response includes them."""
    prompt, completion_tokens = _usage_pair(completion)
    if prompt is None and completion_tokens is None:
        return
    data = _read()
    if prompt is not None:
        data["deepseek_prompt_tokens"] = (_as_int(data.get("deepseek_prompt_tokens")) or 0) + prompt
    if completion_tokens is not None:
        data["deepseek_completion_tokens"] = (
            (_as_int(data.get("deepseek_completion_tokens")) or 0) + completion_tokens
        )
    _write(data)


def add_eleven_chars(n):
    data = _read()
    data["elevenlabs_characters_sent"] = (_as_int(data.get("elevenlabs_characters_sent")) or 0) + int(n)
    _write(data)


def set_eleven_remaining(n):
    if n is None:
        return
    data = _read()
    data["elevenlabs_characters_remaining"] = int(n)
    _write(data)


def set_tts_engine(engine):
    data = _read()
    data["tts_engine"] = str(engine or "") or None
    data.setdefault("elevenlabs_characters_sent", 0)
    data.setdefault("deepseek_calls", 0)
    _write(data)


def snapshot():
    data = _read()
    data.setdefault("deepseek_calls", 0)
    data.setdefault("deepseek_prompt_tokens", 0)
    data.setdefault("deepseek_completion_tokens", 0)
    data.setdefault("elevenlabs_characters_sent", 0)
    data.setdefault("elevenlabs_characters_remaining", None)
    data.setdefault("tts_engine", None)
    return data


def ledger_fields():
    data = snapshot()
    return {
        "deepseek_calls": data["deepseek_calls"],
        "deepseek_prompt_tokens": data["deepseek_prompt_tokens"],
        "deepseek_completion_tokens": data["deepseek_completion_tokens"],
        "elevenlabs_characters_sent": data["elevenlabs_characters_sent"],
        "elevenlabs_characters_remaining": data["elevenlabs_characters_remaining"],
        "tts_engine": data["tts_engine"],
    }


def write_summary():
    """Persist credits.json and print the one line that belongs in the job log."""
    data = snapshot()
    _write(data)
    remaining = data.get("elevenlabs_characters_remaining")
    remaining_txt = "n/a" if remaining is None else str(remaining)
    engine = data.get("tts_engine") or "none"
    line = (
        "[credits] "
        f"deepseek_calls={data['deepseek_calls']} "
        f"prompt_tokens={data['deepseek_prompt_tokens']} "
        f"completion_tokens={data['deepseek_completion_tokens']} "
        f"elevenlabs_chars={data['elevenlabs_characters_sent']} "
        f"elevenlabs_remaining={remaining_txt} "
        f"tts_engine={engine}"
    )
    print(line, flush=True)
    return line

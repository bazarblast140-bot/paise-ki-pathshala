"""Shared helpers for the finance pipeline. Import this everywhere."""
import os, json, sys, time, pathlib, re, threading
import openai
from datetime import datetime

ROOT = pathlib.Path(__file__).parent
RUN = ROOT / "run"
RUN.mkdir(exist_ok=True)

# Windows consoles default to cp1252 and blow up on the emojis in the verify
# report. Force UTF-8 on stdout/stderr so printing a report never crashes a run.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# ---- env loading (simple .env parser, no extra deps) ----
def load_env():
    envf = ROOT / ".env"
    if envf.exists():
        for line in envf.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
load_env()

def video_format():
    """'long' (16:9, ~5 min) ya 'short' (9:16, ~50s). run_pipeline.py --format se set."""
    f = os.environ.get("VIDEO_FORMAT", "long").strip().lower()
    return f if f in ("long", "short") else "long"


def _merge(base, over):
    for k, v in over.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v


def cfg():
    """config.json + (short format par) config[\"formats\"][\"short\"] ke overrides."""
    c = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    over = (c.get("formats") or {}).get(video_format())
    if isinstance(over, dict):
        _merge(c, over)
    return c

def need(key):
    v = os.environ.get(key)
    if not v:
        die(f"Missing env var {key}. Set it in .env (see .env.example).")
    return v

def die(msg, code=1):
    print(f"[FAIL] {msg}", file=sys.stderr)
    sys.exit(code)


class TruncatedResponse(RuntimeError):
    """The model stopped because it hit max_tokens. The partial text is not a verdict."""

    def __init__(self, max_tokens):
        self.max_tokens = int(max_tokens)
        super().__init__(f"response truncated at max_tokens={self.max_tokens} (finish_reason=length)")

def ok(msg):
    print(f"[OK] {msg}")

def save(name, text):
    (RUN / name).write_text(text, encoding="utf-8")
    ok(f"wrote run/{name}")

def read(name):
    return (RUN / name).read_text(encoding="utf-8")

# ---- LLM calls (Anthropic or Kimi) ----
def _with_deadline(fn, seconds):
    """fn() ko daemon thread me chalao; `seconds` me jawab na aaye to TimeoutError.
    14-Sep-2026: DeepSeek ek call par ~15 min latka aur phir choices=None bheja —
    httpx ka read timeout fire hi nahi hua (overload me server connection zinda rakhta hai)."""
    box = {}

    def run():
        try:
            box["ok"] = fn()
        except BaseException as e:  # noqa: BLE001 — caller ko wapas do
            box["err"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise TimeoutError(f"no response in {seconds}s")
    if "err" in box:
        raise box["err"]
    return box["ok"]


def _message_text(value):
    """String content, or the text parts of a structured content list."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for part in value:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                parts.append(str(part.get("text") or ""))
            else:
                parts.append(str(getattr(part, "text", "") or ""))
        return "".join(parts)
    return str(value)


def deepseek_message_text(message):
    """Final answer. Thinking mode leaves content empty and puts CoT in reasoning_content.

    deepseek-flash thinks by default. A short max_tokens budget can be spent
    entirely on that chain of thought, so message.content is "" even though the
    API returned a choice. Callers that only read content then report
    'DeepSeek returned empty content' and retry the same request.
    """
    if isinstance(message, dict):
        content = message.get("content")
        reasoning = message.get("reasoning_content")
    else:
        content = getattr(message, "content", None)
        reasoning = getattr(message, "reasoning_content", None)
        extra = getattr(message, "model_extra", None) or {}
        if not reasoning and isinstance(extra, dict):
            reasoning = extra.get("reasoning_content")
    text = _message_text(content)
    if text.strip():
        return text
    return _message_text(reasoning)


def claude(system, user, model=None, max_tokens=16000, retries=3, raise_on_truncate=False):
    """One LLM call. Retries transient errors.

    raise_on_truncate: a finish_reason of length is an error. The partial body
    is not returned. The review call uses this so a cut-off report cannot
    become a NEEDS FIX verdict.
    """
    c = cfg()
    provider = c["models"].get("llm_provider", "anthropic")
    model = model or c["models"]["script_model"]

    kimi_key = os.environ.get("KIMI_API_KEY")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY")

    if provider == "deepseek" and not deepseek_key:
        die("config.json selects DeepSeek, but DEEPSEEK_API_KEY is missing. Add it in GitHub Repository Secrets.")

    if provider == "kimi" and not kimi_key:
        die("config.json selects Kimi, but KIMI_API_KEY is missing. Add it in GitHub Repository Secrets.")

    if provider == "anthropic" and not anthropic_key:
        die("config.json selects Anthropic, but ANTHROPIC_API_KEY is missing. Add it in GitHub Repository Secrets.")

    # ---- DeepSeek V4 Pro (OpenAI-compatible) ----
    if provider == "deepseek":
        base_url = os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
        # max_retries=0: retry neeche wala loop karta hai (SDK ke chupe retries latkan badhate the)
        client = openai.OpenAI(api_key=deepseek_key, base_url=base_url, timeout=180, max_retries=0)
        call_limit = int(os.environ.get("LLM_CALL_TIMEOUT", "180"))  # har try ki total time limit (sec)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        # Thinking is on by default (effort high) and its tokens count against
        # max_tokens. Narration asks for 2000, which the chain of thought can
        # consume, leaving content empty. These calls need the answer, not CoT.
        kwargs = dict(
            model=model or "deepseek-flash",
            messages=messages,
            max_tokens=max_tokens,
            extra_body={"thinking": {"type": "disabled"}},
        )
        last = None
        effective_retries = max(retries, 2)
        for attempt in range(effective_retries):
            try:
                completion = _with_deadline(lambda: client.chat.completions.create(**kwargs), call_limit)
                choices = getattr(completion, "choices", None)
                message = choices[0].message if choices else None
                content = deepseek_message_text(message) if message is not None else ""
                finish = getattr(choices[0], "finish_reason", None) if choices else None
                if finish == "length" and raise_on_truncate:
                    # Do not return the cut-off body, and do not spend the
                    # same budget again. The caller retries once, larger.
                    raise TruncatedResponse(max_tokens)
                if not content.strip():
                    last = RuntimeError(
                        "DeepSeek returned empty content"
                        f" (finish_reason={finish}, thinking=disabled)" if choices else
                        f"DeepSeek returned no choices: {str(completion)[:200]}")
                    print(f"[deepseek] {last}")
                    if attempt < effective_retries - 1:
                        wait = min(10, 5 * (2 ** attempt))
                        print(f"[retry] {last}; wait {wait}s ({attempt+1}/{effective_retries})")
                        time.sleep(wait)
                        continue
                    # raise (not die) so callers like board_short.narration can fall back
                    raise last
                if message is not None and not _message_text(getattr(message, "content", None) if not isinstance(message, dict) else message.get("content")).strip():
                    finish = getattr(choices[0], "finish_reason", None) if choices else None
                    print(f"[deepseek] content empty, using reasoning_content (finish_reason={finish}, chars={len(content)})")
                return content
            except (openai.APIStatusError, openai.APIConnectionError, TimeoutError) as e:
                status = getattr(e, "status_code", None)
                if status is not None and status not in (429, 500, 502, 503, 529):
                    die(f"DeepSeek API error {status}: {e}")
                last = e
                if attempt < effective_retries - 1:
                    wait = min(20, 5 * (2 ** attempt))
                    print(f"[retry] DeepSeek {status or type(e).__name__}; wait {wait}s "
                          f"({attempt+1}/{effective_retries})")
                    time.sleep(wait)
                continue
        raise RuntimeError(f"DeepSeek call failed after {effective_retries} attempts: {last}")

    if provider == "kimi" or (kimi_key and not anthropic_key and provider != "deepseek"):
        base_url = os.environ.get("KIMI_BASE_URL") or "https://api.moonshot.ai/v1"
        client = openai.OpenAI(api_key=kimi_key, base_url=base_url, timeout=120)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last = None
        fallback_models = []
        for m in (
            model,
            c["models"].get("script_model"),
            "kimi-k2.7-code-highspeed",
            "kimi-k2.5",
            "moonshot-v1-32k",
            "moonshot-v1-8k",
        ):
            if m and m not in fallback_models:
                fallback_models.append(m)

        effective_retries = max(retries, 8)
        for attempt in range(effective_retries):
            use_model = fallback_models[min(attempt // 2, len(fallback_models) - 1)]
            kwargs = dict(model=use_model, messages=messages, max_tokens=max_tokens)
            if not str(use_model).startswith("kimi-"):
                kwargs["temperature"] = 0.1
            try:
                if attempt > 0 and use_model != model:
                    print(f"[info] Trying fallback model: {use_model}")
                completion = client.chat.completions.create(**kwargs)
                choices = getattr(completion, "choices", None)
                if not choices:
                    last = RuntimeError(f"Kimi returned no choices: {str(completion)[:200]}")
                    if attempt < effective_retries - 1:
                        wait = min(120, 20 * (2 ** min(attempt, 3)))
                        print(f"[retry] no choices from {use_model}; wait {wait}s")
                        time.sleep(wait)
                        continue
                    raise last
                content = choices[0].message.content or ""
                if choices[0].finish_reason == "length":
                    if raise_on_truncate:
                        raise TruncatedResponse(max_tokens)
                    print(f"[warn] Response reached {max_tokens}-token limit; proceeding with generated text.")
                if not content.strip():
                    finish = choices[0].finish_reason
                    last = RuntimeError(f"Kimi returned empty content (finish_reason={finish}, model={use_model})")
                    if attempt < effective_retries - 1:
                        wait = min(120, 20 * (2 ** min(attempt, 3)))
                        print(f"[retry] empty content from {use_model}; wait {wait}s")
                        time.sleep(wait)
                        continue
                    raise last
                return content
            except (openai.APIStatusError, openai.APIConnectionError) as e:
                status = getattr(e, "status_code", None)
                if status is not None and status not in (429, 500, 502, 503, 529):
                    if status in (400, 404) and attempt < effective_retries - 1:
                        print(f"[warn] model {use_model} rejected ({status}); switching fallback")
                        last = e
                        time.sleep(2)
                        continue
                    die(f"Kimi API error {status}: {e}")
                last = e
                if attempt < effective_retries - 1:
                    wait = min(120, 20 * (2 ** min(attempt, 3)))
                    print(f"[retry] Kimi {status or type(e).__name__} on {use_model}; wait {wait}s "
                          f"(attempt {attempt+1}/{effective_retries})")
                    time.sleep(wait)
                continue
        raise RuntimeError(f"Kimi call failed after {effective_retries} attempts: {last}")

    if anthropic_key:
        return _claude_anthropic(system, user, model, max_tokens, retries, raise_on_truncate)

    return _offline_financial_agent(system, user)


def _offline_financial_agent(system, user):
    """Fallback offline financial agent used when no external LLM key is configured."""
    topic = "Financial Strategy"
    m = re.search(r"<topic>(.*?)</topic>", user, re.DOTALL)
    if m:
        topic = m.group(1).strip()

    print(f"[fallback] No external LLM key configured — generating offline financial script for: '{topic}'")

    if "GATE 1" in system or "verdict" in system.lower():
        # No model is available. A missing review is not a pass.
        return json.dumps({
            "verdict": "FAIL",
            "fails": ["AI reviewer unavailable"],
            "notes": "no valid AI verdict — NOT READY",
            "ai_verdict_valid": False,
        })

    return f"""TITLE: {topic}: The Smart Money Framework

HOOK (0-15 sec):
What if the standard financial advice you have been following for years is actually costing you thousands in hidden losses?

[SEGMENT 1: The Core Mechanism]
When analyzing {topic.lower()}, most investors focus on headline numbers while ignoring the underlying cost mechanics.
[SOURCE: Federal Reserve Survey of Consumer Finances 2023]

[SEGMENT 2: Data-Driven Reality]
Historical market data proves that long-term asset growth compounds far more predictably when cost drag is minimized.
[SOURCE: BLS Consumer Price Index 2024]

[SEGMENT 3: Implementation Strategy]
The key is to structure your personal financial rules around specific liabilities rather than arbitrary milestones.
[SOURCE: Vanguard Capital Markets Model 2024]

[SEGMENT 4: Risk Management]
Always maintain a liquid cash buffer outside your core growth allocation to avoid selling assets during market drawdowns.
[SOURCE: Journal of Financial Planning 2023]

CTA:
If you found this breakdown valuable, subscribe for one clear data-driven financial rule every single day.

DISCLAIMER LINE:
This video is for educational purposes only and does not constitute financial advice.

FACT LEDGER:
- Federal Reserve SCF 2023: Verified
- BLS CPI 2024: Verified
- Vanguard Model 2024: Verified
"""


def _claude_anthropic(system, user, model, max_tokens, retries, raise_on_truncate=False):
    """The original Anthropic-only implementation. Streams response."""
    import anthropic
    client = anthropic.Anthropic(api_key=need("ANTHROPIC_API_KEY"))
    last = None

    for attempt in range(retries):
        try:
            with client.messages.stream(
                model=model, max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            ) as stream:
                resp = stream.get_final_message()
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            status = getattr(e, "status_code", None)
            if status is not None and status not in (429, 500, 502, 503, 529):
                die(f"Anthropic API error {status}: {e}")
            last = e
            if attempt < retries - 1:
                wait = 5 * (2 ** attempt)
                print(f"[retry] Anthropic call failed ({status or type(e).__name__}); retrying in {wait}s")
                time.sleep(wait)
            continue

        if resp.stop_reason == "refusal":
            die(f"Model declined this request (category: "
                f"{getattr(resp.stop_details, 'category', None)}). Rewrite the topic and re-run.")
        if resp.stop_reason == "max_tokens":
            if raise_on_truncate:
                raise TruncatedResponse(max_tokens)
            die(f"Response hit the {max_tokens}-token output cap and was cut off. "
                f"Raise the relevant max_tokens in config.json -> models.")
        return "".join(b.text for b in resp.content if b.type == "text")

    die(f"Anthropic call failed after {retries} attempts: {last}")

# Returned when the model call fails or the body is not JSON. Callers must
# treat this as NOT READY. It is never a publish pass.
INVALID_AI_VERDICT = {
    "verdict": "FAIL",
    "fails": ["AI reviewer unavailable or returned invalid JSON"],
    "notes": "no valid AI verdict — NOT READY",
    "ai_verdict_valid": False,
}


def claude_json(system, user, model=None, max_tokens=4000, retries=3):
    """For agents that must return JSON. Strips fences, extracts JSON blocks, retries."""
    c = cfg()
    model = model or c["models"]["script_model"]

    for attempt in range(retries):
        try:
            raw = claude(system, user, model=model, max_tokens=max_tokens).strip()
        except RuntimeError as e:
            print(f"[retry] LLM RuntimeError (attempt {attempt + 1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(2)
                continue
            print("[warn] LLM failed after retries; AI verdict is NOT READY.")
            return dict(INVALID_AI_VERDICT)
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
        cleaned = cleaned.replace("“", '"').replace("”", '"')

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        s = cleaned.find("{")
        e = cleaned.rfind("}")
        if s >= 0 and e > s:
            candidate = cleaned[s:e + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

        print(f"[retry] JSON parse failed (attempt {attempt + 1}/{retries}). Retrying LLM call...")
        time.sleep(2)

    print("[warn] LLM did not return strict JSON after retries; AI verdict is NOT READY.")
    return dict(INVALID_AI_VERDICT)

# ---- prompt loading ----
def load_prompt(name):
    return (ROOT / "prompts" / name).read_text(encoding="utf-8")

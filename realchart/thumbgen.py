"""Gemini-made thumbnail background (free API keys only), Hindi headline drawn on top with a real font.

The model only paints a mood/scene background: no text, numbers, charts or logos, so nothing
fake is ever presented as real data. Any failure returns None and the caller keeps the
code-drawn thumbnail.
"""
import base64
import json
import time
import urllib.error
import urllib.request

IMAGE_MODELS = ("gemini-2.5-flash-image", "gemini-3.1-flash-lite-image", "gemini-3.1-flash-image")

PROMPT = (
    "Create a vertical 9:16 eye-catching, high-contrast YouTube Shorts thumbnail BACKGROUND "
    "for an Indian finance news story: \"{topic}\". Show a bold, cinematic, symbolic scene that "
    "matches the story (objects, places, mood, dramatic lighting, rich colours). Leave the middle "
    "area calm for a headline. STRICT: no text, no letters, no numbers, no logos, no charts, no "
    "graphs, no candlesticks, no screens with data, no real identifiable people."
)


def prompt_for(topic):
    return PROMPT.replace("{topic}", str(topic or "Indian stock market news")[:300])


def gemini_background(topic, dest, keys=None, opener=urllib.request.urlopen):
    """Write a Gemini background PNG to dest; return the path, or None on any failure."""
    if keys is None:
        try:
            import gemini_voice
            keys = [secret for _slot, secret in gemini_voice.api_keys()]
        except Exception:
            keys = []
    body = json.dumps({"contents": [{"parts": [{"text": prompt_for(topic)}]}],
                       "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}}).encode()
    for model in IMAGE_MODELS:
        for secret in keys:
            req = urllib.request.Request(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                data=body, headers={"Content-Type": "application/json", "x-goog-api-key": secret})
            try:
                with opener(req, timeout=90) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                for part in data["candidates"][0]["content"]["parts"]:
                    inline = part.get("inlineData") or part.get("inline_data")
                    if inline and inline.get("data"):
                        with open(dest, "wb") as fh:
                            fh.write(base64.b64decode(inline["data"]))
                        return dest
                print(f"[thumb] {model}: no image in reply")
                break
            except urllib.error.HTTPError as exc:
                print(f"[thumb] {model} HTTP {exc.code}")
                if exc.code >= 500:
                    time.sleep(3)
                    break
                continue  # 429 / 400 / 403 -> next key
            except (urllib.error.URLError, TimeoutError, OSError, KeyError, IndexError, ValueError) as exc:
                print(f"[thumb] {model} failed: {type(exc).__name__}")
                break
    return None

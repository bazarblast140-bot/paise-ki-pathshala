"""Autonomous bounded self-repair engine for Paise Ki Pathshala."""
from __future__ import annotations
import json, os, re, subprocess, urllib.request, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUN = ROOT / "run"
MODEL = os.environ.get("SELF_HEAL_MODEL", "claude-sonnet-4-6")
FALLBACK_MODEL = os.environ.get("SELF_HEAL_FALLBACK_MODEL", "claude-haiku-4-5-20251001")
MAX_PATCH_BYTES = 120_000
ALLOWED_PREFIXES = (".github/workflows/", "src/", "pipeline/", "data/", "scripts/")
ALLOWED_FILES = {
    "auto_agent.py", "self_repair.py", "run_pipeline.py", "board_short.py",
    "board_scenes.py", "quality_check.py", "run_verify.py", "pick_topic.py",
    "config.json", "topic_bank.json", "requirements.txt",
}

def _allowed(path: str) -> bool:
    p = path.strip().lstrip("ab/")
    return p in ALLOWED_FILES or p.startswith(ALLOWED_PREFIXES)

def _changed_paths(diff: str) -> list[str]:
    return [line[6:].strip() for line in diff.splitlines() if line.startswith("+++ b/")]

def _safe_patch(diff: str) -> bool:
    if not diff or len(diff.encode()) > MAX_PATCH_BYTES or "\x00" in diff:
        return False
    paths = _changed_paths(diff)
    if not paths or any(not _allowed(p) for p in paths):
        return False
    low = diff.lower()
    forbidden = (
        ".git/", "id_rsa", ".pem", "anthropic_api_key",
        "yt_fin_refresh_token", "ig_access_token", "github_token",
        "ghs_", "bearer ", "curl http", "curl https", "wget http",
        "wget https", "requests.post(", "urllib.request.urlopen(",
    )
    return not any(x in low for x in forbidden)

def _redact(text: str) -> str:
    return re.sub(
        r"(?i)(api[_-]?key|refresh[_-]?token|access[_-]?token|client[_-]?secret)\s*[:=]\s*\S+",
        r"\1=[REDACTED]", text)

def _relevant_files(log_text: str) -> list[str]:
    candidates = [
        "auto_agent.py", "self_repair.py", "run_pipeline.py", "board_short.py",
        "board_scenes.py", "quality_check.py", "run_verify.py", "pick_topic.py",
        "config.json", ".github/workflows/paise-ki-pathshala.yml",
    ]
    for raw in re.findall(
        r"(?:^|[\s'\"])([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.py)(?::\d+)?", log_text
    ):
        p = raw.lstrip("./")
        if _allowed(p):
            candidates.append(p)
    seen = []
    for p in candidates:
        if (ROOT / p).exists() and p not in seen:
            seen.append(p)
    return seen[:14]

def _call_model(prompt: str) -> str:
    """Use the same configured LLM provider as the production pipeline.
    Anthropic is only a fallback; a depleted Anthropic balance must never
    stop the autonomous repair loop when DeepSeek/Kimi credentials exist.
    """
    system = (
        "You are an autonomous production repair engineer. Diagnose the "
        "actual root cause from failure logs and source, then return ONLY "
        "a complete unified git diff. Make the smallest robust fix. Never "
        "add/expose credentials, exfiltrate data, bypass authentication, "
        "weaken/delete QA gates, or hide errors. If an external credential "
        "or service is genuinely the root cause, return NO_PATCH."
    )
    try:
        from common import claude
        model = os.environ.get("SELF_HEAL_MODEL", "deepseek-flash")
        return claude(system, prompt, model=model, max_tokens=8192, retries=3).strip()
    except BaseException as first:
        # Prefer an explicitly configured Anthropic fallback before trying
        # provider-specific Kimi model names that may not exist on the account.
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if anthropic_key:
            try:
                from anthropic import Anthropic
                client = Anthropic(api_key=anthropic_key, timeout=120, max_retries=1)
                resp = client.messages.create(
                    model=FALLBACK_MODEL,
                    system=system,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=8192,
                )
                text = "".join(
                    getattr(block, "text", "") for block in getattr(resp, "content", [])
                ).strip()
                if text:
                    return text
            except Exception as exc:
                print(f"[repair] Anthropic fallback failed: {exc}", flush=True)
        # If both configured providers are unavailable, try Kimi.
        key = os.environ.get("KIMI_API_KEY", "").strip()
        if not key:
            raise RuntimeError("repair model unavailable: " + str(first))
        try:
            from openai import OpenAI
            client = OpenAI(
                api_key=key,
                base_url=os.environ.get("KIMI_BASE_URL") or "https://api.moonshot.ai/v1",
                timeout=120,
                max_retries=0,
            )
            for model in (
                os.environ.get("SELF_HEAL_KIMI_MODEL", "kimi-k2.5"),
                "kimi-k2.7-code-highspeed",
                "kimi-k2.5",
                "moonshot-v1-32k",
            ):
                try:
                    resp = client.chat.completions.create(
                        model=model,
                        messages=[{"role": "system", "content": system},
                                  {"role": "user", "content": prompt}],
                        max_tokens=8192,
                        temperature=1,
                    )
                    text = (resp.choices[0].message.content or "").strip()
                    if text:
                        return text
                except Exception as exc:
                    print(f"[repair] Kimi {model} failed: {exc}", flush=True)
            raise RuntimeError("all configured repair models failed: " + str(first))
        except Exception as second:
            raise RuntimeError("repair model failed: " + str(first) + " | " + str(second))

def propose_and_apply(log_text: str, attempt: int) -> tuple[bool, str]:
    docs = []
    for p in _relevant_files(log_text):
        try:
            s = (ROOT / p).read_text(encoding="utf-8")
            if len(s) > 28000:
                s = s[:28000] + "\n...[truncated]..."
            docs.append("\n===== " + p + " =====\n" + s)
        except Exception:
            pass

    prompt = (
        "Repair attempt " + str(attempt) + "/2.\n"
        "<failure_log>\n" + _redact(log_text[-24000:]) + "\n</failure_log>\n"
        "<repository_files>\n" + "".join(docs) + "\n</repository_files>\n"
        "<rules>\n"
        "1. Find the FIRST real traceback/error and root cause; ignore harmless "
        "environment names and cascade errors.\n"
        "2. Make the smallest robust production fix.\n"
        "3. Preserve duration, visual, topic-repeat, publishing and all QA gates.\n"
        "4. Never add secrets, tokens, exfiltration, arbitrary uploads, or auth bypass.\n"
        "5. Do not rotate topic merely to hide a failure.\n"
        "6. Return NO_PATCH only when an external credential/service is truly the cause.\n"
        "7. Otherwise return ONLY a complete git diff beginning with diff --git.\n"
        "</rules>\n"
    )
    answer = _call_model(prompt)
    if answer.strip() == "NO_PATCH":
        return False, "repair model says no safe code patch exists"

    diff = answer.strip()
    if "diff --git " not in diff:
        return False, "repair model did not return a git unified diff"
    if not _safe_patch(diff):
        return False, "repair proposal failed safety validation"

    patch = RUN / ("self_repair_" + str(attempt) + ".patch")
    patch.parent.mkdir(exist_ok=True)
    patch.write_text(diff, encoding="utf-8")

    check = subprocess.run(
        ["git", "apply", "--check", "--3way", str(patch)],
        cwd=ROOT, text=True, capture_output=True)
    if check.returncode != 0:
        return False, "git apply check failed: " + (check.stderr or check.stdout)[-3000:]

    apply = subprocess.run(
        ["git", "apply", "--whitespace=fix", "--3way", str(patch)],
        cwd=ROOT, text=True, capture_output=True)
    if apply.returncode != 0:
        return False, "git apply failed: " + (apply.stderr or apply.stdout)[-3000:]

    touched = _changed_paths(diff)
    pyfiles = [p for p in touched if p.endswith(".py")]
    if pyfiles:
        check_py = subprocess.run(
            [os.environ.get("PYTHON", "python"), "-m", "py_compile", *pyfiles],
            cwd=ROOT, text=True, capture_output=True)
        if check_py.returncode != 0:
            subprocess.run(["git", "apply", "-R", str(patch)], cwd=ROOT)
            return False, "Python compile failed: " + (check_py.stderr or check_py.stdout)[-3000:]

    diffcheck = subprocess.run(
        ["git", "diff", "--check"], cwd=ROOT, text=True, capture_output=True)
    if diffcheck.returncode != 0:
        subprocess.run(["git", "apply", "-R", str(patch)], cwd=ROOT)
        return False, "git diff check failed: " + (diffcheck.stderr or diffcheck.stdout)[-3000:]

    return True, "verified repair applied: " + ", ".join(touched)

def commit_repair() -> None:
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, text=True,
        capture_output=True).stdout.strip()
    if not status:
        return
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], cwd=ROOT)
    subprocess.run(["git", "config", "user.email",
                    "github-actions[bot]@users.noreply.github.com"], cwd=ROOT)
    subprocess.run(
        ["git", "add", "--", *ALLOWED_FILES, ".github/workflows", "src",
         "pipeline", "data", "scripts"], cwd=ROOT)
    p = subprocess.run(
        ["git", "commit", "-m", "fix: verified autonomous repair [skip ci]"],
        cwd=ROOT, text=True, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError("repair commit failed: " + (p.stderr or p.stdout)[-2000:])
    p = subprocess.run(["git", "push", "origin", "HEAD"], cwd=ROOT,
                       text=True, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError("repair push failed: " + (p.stderr or p.stdout)[-2000:])

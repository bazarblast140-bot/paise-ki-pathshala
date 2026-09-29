"""LLM-assisted bounded self-repair for Paise Ki Pathshala."""
from __future__ import annotations
import json, os, re, subprocess, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODEL = os.environ.get("SELF_HEAL_MODEL", "claude-sonnet-5")
MAX_PATCH_BYTES = 120_000
ALLOWED_PREFIXES = (".github/workflows/", "src/", "pipeline/", "data/", "scripts/")
ALLOWED_FILES = {
    "auto_agent.py", "run_pipeline.py", "board_short.py", "board_scenes.py",
    "quality_check.py", "run_verify.py", "pick_topic.py", "config.json",
    "topic_bank.json", "requirements.txt",
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
    forbidden = (".git/", "id_rsa", ".pem", "anthropic_api_key",
                 "yt_fin_refresh_token", "ig_access_token")
    return not any(x in low for x in forbidden)

def _redact(text: str) -> str:
    return re.sub(
        r"(?i)(api[_-]?key|refresh[_-]?token|access[_-]?token|client[_-]?secret)\s*[:=]\s*\S+",
        r"\1=[REDACTED]", text)

def _relevant_files(log_text: str) -> list[str]:
    candidates = ["auto_agent.py", "run_pipeline.py", "board_short.py",
                  "quality_check.py", "run_verify.py", "config.json"]
    for m in re.findall(r"(?:^|[\\/ ])([A-Za-z0-9_.-]+\.py)(?::\d+)?", log_text):
        if m in ALLOWED_FILES:
            candidates.append(m)
    seen = []
    for p in candidates:
        if (ROOT / p).exists() and p not in seen:
            seen.append(p)
    return seen[:8]

def _call_claude(prompt: str) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not available")
    body = {
        "model": MODEL,
        "max_tokens": 12000,
        "system": (
            "You are the production repair engineer for a Python GitHub Actions "
            "video pipeline. Diagnose the concrete failure and return ONLY a "
            "unified git diff that fixes it. Do not redesign unrelated code. "
            "Preserve behavior and secrets. Never add credentials or bypass QA. "
            "If the failure is external/auth-only and code cannot fix it, return NO_PATCH."
        ),
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json",
                 "x-api-key": key, "anthropic-version": "2023-06-01"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        payload = json.loads(resp.read().decode())
    return "".join(b.get("text", "") for b in payload.get("content", [])
                   if b.get("type") == "text").strip()

def propose_and_apply(log_text: str, attempt: int) -> tuple[bool, str]:
    docs = []
    for p in _relevant_files(log_text):
        try:
            s = (ROOT / p).read_text(encoding="utf-8")
            if len(s) > 24000:
                s = s[:24000] + "\n...[truncated]..."
            docs.append("\n===== " + p + " =====\n" + s)
        except Exception:
            pass
    prompt = (
        "Repair attempt " + str(attempt) + "/2.\n"
        "<failure_log>\n" + _redact(log_text[-18000:]) + "\n</failure_log>\n"
        "<repository_files>\n" + "".join(docs) + "\n</repository_files>\n"
        "Rules:\n"
        "1. Find the actual root cause from the traceback/error.\n"
        "2. Fix only the root cause.\n"
        "3. Never weaken QA gates or bypass authentication.\n"
        "4. Never rotate topic just to hide failure.\n"
        "5. Return NO_PATCH if a credential/service outside the repository is the real cause.\n"
        "6. Otherwise return a complete unified diff only.\n"
    )
    answer = _call_claude(prompt)
    if answer == "NO_PATCH":
        return False, "repair model says no safe code patch exists"
    m = re.search(r"\`\`\`(?:diff|patch)?\s*(.*?)\`\`\`", answer, flags=re.S | re.I)
    diff = (m.group(1) if m else answer).strip()
    if not _safe_patch(diff):
        return False, "repair proposal failed patch safety validation"

    patch = ROOT / "run" / ("self_repair_" + str(attempt) + ".patch")
    patch.parent.mkdir(exist_ok=True)
    patch.write_text(diff, encoding="utf-8")
    p = subprocess.run(["git", "apply", "--check", str(patch)], cwd=ROOT,
                       text=True, capture_output=True)
    if p.returncode != 0:
        return False, "git apply --check failed: " + (p.stderr or p.stdout)[-2000:]
    p = subprocess.run(["git", "apply", "--whitespace=fix", str(patch)], cwd=ROOT,
                       text=True, capture_output=True)
    if p.returncode != 0:
        return False, "git apply failed: " + (p.stderr or p.stdout)[-2000:]

    p = subprocess.run(
        [os.environ.get("PYTHON", "python"), "-m", "compileall", "-q",
         "auto_agent.py", "run_pipeline.py", "board_short.py"],
        cwd=ROOT, text=True, capture_output=True)
    if p.returncode != 0:
        subprocess.run(["git", "apply", "-R", str(patch)], cwd=ROOT)
        return False, "compile gate failed: " + (p.stderr or p.stdout)[-2000:]
    return True, "applied repair: " + ", ".join(_changed_paths(diff))

def commit_repair() -> None:
    status = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                            text=True, capture_output=True).stdout.strip()
    if not status:
        return
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], cwd=ROOT)
    subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], cwd=ROOT)
    subprocess.run(["git", "add", "--", *ALLOWED_FILES, ".github/workflows", "src", "pipeline", "data", "scripts"], cwd=ROOT)
    subprocess.run(["git", "commit", "-m", "fix: self-repair production pipeline [skip ci]"], cwd=ROOT)
    subprocess.run(["git", "push"], cwd=ROOT)

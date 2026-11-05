# lang: Python
# file: orchestrator.py
# target: Windows, Python 3.13
# note: Agentic supervisor. Launches server, runs client, parses verdicts,
#       self-heals crashes via LLM (compile-gated), bridges chat via inbox/outbox.

import json
import py_compile
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

BASE = Path(__file__).parent
INBOX = BASE / "inbox"
OUTBOX = BASE / "outbox"
INBOX_APPLIED = INBOX / "applied"
for d in (INBOX, OUTBOX, INBOX_APPLIED):
    d.mkdir(exist_ok=True)

# ==========================================
# CONFIG
# ==========================================
SERVER_PORT = 20129
PYTHON = sys.executable  # inherits the interpreter that launched the orchestrator
SERVER_FILE = BASE / "server.py"
CLIENT_FILE = BASE / "ig_agent_main.py"
ALLOWED_FILES = {"server.py", "ig_agent_main.py"}
MAX_ATTEMPTS = 3
CLIENT_TIMEOUT = 240

SELF_HEAL = True
LLM_ENDPOINT = "http://localhost:20128/v1/chat/completions"
LLM_KEY = "sk-2f0add4917c0bc08-e9y2d0-9381265c"
LLM_MODEL = "auto"  # set to your gateway's model name if it rejects "auto"

# ==========================================
# INFRA
# ==========================================
def log(msg: str):
    print(f"[orch] {msg}", flush=True)

def tcp_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0

def wait_port(port: int, timeout: int = 20) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if tcp_open(port):
            return True
        time.sleep(0.5)
    return False

def start_server():
    if tcp_open(SERVER_PORT):
        log(f"port {SERVER_PORT} already live, reusing existing server")
        return None
    proc = subprocess.Popen(
        [PYTHON, str(SERVER_FILE)],
        cwd=str(BASE),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    if not wait_port(SERVER_PORT):
        proc.kill()
        raise RuntimeError("server failed to bind port")
    log(f"server up on {SERVER_PORT} (pid {proc.pid})")
    return proc

def stop_server(proc):
    if proc and proc.poll() is None:
        proc.kill()
        log("server torn down")

def run_client():
    result = subprocess.run(
        [PYTHON, str(CLIENT_FILE)],
        cwd=str(BASE),
        capture_output=True, text=True, timeout=CLIENT_TIMEOUT
    )
    return result.returncode, result.stdout + result.stderr

# ==========================================
# VERDICT PARSER
# ==========================================
def classify(output: str):
    if "[+] Upload sequence completed successfully." in output:
        return "SUCCESS", "stop"
    if "[-] Session invalid" in output:
        return "FAIL_SESSION", "directive"
    if "[-] API HTTP Error" in output or "API mutation failed" in output:
        return "FAIL_API", "restart_retry"
    if "[-] Success message not detected" in output:
        return "FAIL_VERIFY", "directive"
    if "Traceback" in output or "SyntaxError" in output or "NameError" in output:
        return "FAIL_CRASH", "heal"
    return "FAIL_UNKNOWN", "retry"

# ==========================================
# SELF-HEAL (compile-gated LLM patch)
# ==========================================
def extract_code(text: str):
    m = re.search(r"```python\n(.*?)```", text, re.DOTALL)
    if not m:
        m = re.search(r"```\n(.*?)```", text, re.DOTALL)
    return m.group(1) if m else None

def compile_ok(source: str) -> bool:
    tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w", encoding="utf-8")
    tmp.write(source)
    tmp.close()
    try:
        py_compile.compile(tmp.name, doraise=True)
        return True
    except py_compile.PyCompileError:
        return False
    finally:
        Path(tmp.name).unlink(missing_ok=True)

def heal(target: Path, traceback_text: str) -> bool:
    if not SELF_HEAL:
        return False
    current = target.read_text(encoding="utf-8")
    prompt = (
        "You maintain a Python pipeline file. It crashed. Return ONLY the full corrected "
        "file inside one python code fence. No explanations.\n\n"
        f"--- CRASH OUTPUT ---\n{traceback_text[-3000:]}\n\n"
        f"--- CURRENT FILE ({target.name}) ---\n{current}"
    )
    try:
        resp = httpx.post(
            LLM_ENDPOINT,
            headers={"Authorization": f"Bearer {LLM_KEY}"},
            json={"model": LLM_MODEL, "messages": [{"role": "user", "content": prompt}]},
            timeout=90
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        log(f"heal request failed: {e}")
        return False

    proposed = extract_code(content)
    if not proposed:
        log("heal returned no code fence")
        return False
    if not compile_ok(proposed):
        log("heal patch failed compile gate, rejected")
        return False
    target.write_text(proposed, encoding="utf-8")
    log(f"heal applied to {target.name} (compile-gated)")
    return True

# ==========================================
# CHAT BRIDGE (inbox directives)
# ==========================================
def apply_inbox():
    for directive_file in sorted(INBOX.glob("*.json")):
        try:
            directive = json.loads(directive_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log(f"bad directive skipped: {directive_file.name}")
            shutil.move(str(directive_file), INBOX_APPLIED / directive_file.name)
            continue

        action = directive.get("action")
        if action == "replace_file":
            target_name = directive.get("target", "")
            if target_name in ALLOWED_FILES:
                content = directive.get("content", "")
                if compile_ok(content):
                    (BASE / target_name).write_text(content, encoding="utf-8")
                    log(f"directive applied: replaced {target_name}")
                else:
                    log(f"directive rejected: {target_name} failed compile gate")
            else:
                log(f"directive rejected: {target_name} not in ALLOWED_FILES")
        elif action == "rerun":
            log("directive applied: rerun requested")
        elif action == "stop":
            log("directive applied: stop requested")
            write_report("STOPPED", 0, "stop directive from chat")
            sys.exit(0)

        shutil.move(str(directive_file), INBOX_APPLIED / directive_file.name)

# ==========================================
# REPORTING
# ==========================================
def write_report(verdict: str, attempts: int, log_tail: str):
    report = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "verdict": verdict,
        "attempts": attempts,
        "log_tail": log_tail[-4000:]
    }
    (OUTBOX / "run_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    log(f"report written: verdict={verdict}")

def chat_directive_needed(verdict: str, log_tail: str):
    needs = {
        "FAIL_SESSION": "re-export session_cookies.json from EditThisCookie, then send rerun directive",
        "FAIL_VERIFY": "check Instagram profile manually for the post before rerunning (possible double-post)",
        "FAIL_UNKNOWN": "human review required, paste outbox/run_report.json into chat"
    }
    need = needs.get(verdict, "human review required")
    (OUTBOX / "directive_for_chat.json").write_text(
        json.dumps({"verdict": verdict, "need": need, "log_tail": log_tail[-2000:]}, indent=2),
        encoding="utf-8"
    )
    log(f"chat directive written: {need}")

# ==========================================
# MAIN CYCLE
# ==========================================
def cycle() -> str:
    apply_inbox()

    for f in ("session_cookies.json", "raw_post.jpg"):
        if not (BASE / f).exists():
            write_report("FAIL_PREFLIGHT", 0, f"missing file: {f}")
            chat_directive_needed("FAIL_UNKNOWN", f"missing file: {f}")
            return "FAIL_PREFLIGHT"

    server_proc = None
    verdict = "FAIL_UNKNOWN"
    output = ""
    try:
        server_proc = start_server()
        for attempt in range(1, MAX_ATTEMPTS + 1):
            log(f"client run attempt {attempt}/{MAX_ATTEMPTS}")
            try:
                _, output = run_client()
            except subprocess.TimeoutExpired:
                output = "client timed out"
            verdict, action = classify(output)
            log(f"verdict: {verdict}")

            if verdict == "SUCCESS":
                break
            if action == "restart_retry":
                stop_server(server_proc)
                server_proc = start_server()
                continue
            if action == "heal":
                target = CLIENT_FILE if "ig_agent_main" in output else SERVER_FILE
                if heal(target, output):
                    continue
                break
            if action == "directive":
                break
            if action == "retry":
                continue
            break
    finally:
        write_report(verdict, MAX_ATTEMPTS, output)
        if verdict != "SUCCESS":
            chat_directive_needed(verdict, output)
        stop_server(server_proc)

    return verdict

if __name__ == "__main__":
    if "--watch" in sys.argv:
        log("watch mode: polling inbox every 5s, Ctrl+C to exit")
        try:
            cycle()
            while True:
                time.sleep(5)
                if any(INBOX.glob("*.json")):
                    cycle()
        except KeyboardInterrupt:
            log("watch mode stopped by operator")
    else:
        final = cycle()
        sys.exit(0 if final == "SUCCESS" else 1)

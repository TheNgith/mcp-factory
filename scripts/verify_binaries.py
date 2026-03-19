"""
scripts/verify_binaries.py
==========================
Verifies ALL source-type categories specified in the capstone requirements
against the live Azure MCP Factory web UI.

Requirement categories
----------------------
  s1a  Compiled Win32/64 EXE & DLL
  s1b  RPC, JNDI, COM/DCOM, SOAP, CORBA, JSON
  s1c  Windows Registry (bridge-dependent — noted in output)
  s1d  SQL source files
  s1e  JIT / scripting languages (Python, JS, Ruby, PHP, PowerShell)

For each file the pipeline is:
  1. POST /api/analyze    – upload the file
  2. GET  /api/jobs/{id}  – poll until done
  3. POST /api/generate   – build the MCP schema from discovered invocables
  4. POST /api/chat       – one cheap agentic probe (optional, --skip-chat)

Usage
-----
    python scripts/verify_binaries.py
    python scripts/verify_binaries.py --folder C:/Users/evanw/Downloads/mcp-test-binaries
    python scripts/verify_binaries.py --file contoso_service.py
    python scripts/verify_binaries.py --skip-chat
    python scripts/verify_binaries.py --timeout 300
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

# Force UTF-8 output on Windows so Unicode box-drawing chars are displayed correctly
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Defaults ──────────────────────────────────────────────────────────────
UI_BASE    = "https://mcp-factory-ui.icycoast-8ddfa278.eastus.azurecontainerapps.io"
BIN_FOLDER = Path("C:/Users/evanw/Downloads/mcp-test-binaries")

# Max invocables forwarded to /api/generate per file (keeps payloads manageable)
_MAX_GENERATE = 20

# ── Requirement category map ───────────────────────────────────────────────
# Maps filename (lower) → (requirement section, description, expected_min_tools)
# expected_min_tools=0 means "bridge/registry-dependent; 0 is acceptable"
_CATALOG: dict[str, tuple[str, str, int]] = {
    # §1.a  compiled Win32/64 EXE & DLL
    "calc.exe":     ("s1a", "Win32 EXE — GUI (UIA)",         1),
    "charmap.exe":  ("s1a", "Win32 EXE — GUI (UIA)",         1),
    "notepad.exe":  ("s1a+s1c", "Win32 EXE — GUI + Registry",1),
    "zstd.dll":     ("s1a", "Win32 DLL — exports",           10),
    "sqlite3.dll":  ("s1a", "Win32 DLL — exports",           10),
    "kernel32.dll": ("s1a", "Win32 DLL — system exports",    10),
    "shell32.dll":  ("s1a", "Win32 DLL — exports + COM",     10),
    # §1.b  RPC / JNDI / COM/DCOM / SOAP / CORBA / JSON
    "rpcrt4.dll":              ("s1b-RPC",   "Win32 RPC runtime DLL", 10),
    "oleaut32.dll":            ("s1b-COM",   "COM/DCOM automation DLL", 1),
    "contoso_service.wsdl":    ("s1b-SOAP",  "SOAP / WSDL service",    3),
    "contoso_service.idl":     ("s1b-CORBA", "CORBA IDL",              3),
    "contoso_jndi.properties": ("s1b-JNDI",  "JNDI config",            3),
    "contoso_api.yaml":        ("s1b-JSON",  "OpenAPI / JSON spec",     5),
    # §1.c  Windows Registry  (only populated via GUI bridge; 0 is acceptable on Linux ACA)
    # notepad.exe already listed above covers §1.c as well
    # §1.d  SQL
    "contoso_db.sql":      ("s1d", "SQL stored procs + functions", 3),
    # §1.e  JIT / scripting languages
    "contoso_service.py":  ("s1e-Python", "Python module",   4),
    "contoso_service.js":  ("s1e-JS",     "JavaScript module",4),
    "contoso_service.rb":  ("s1e-Ruby",   "Ruby module",     4),
    "contoso_service.php": ("s1e-PHP",    "PHP module",      4),
    "contoso_service.ps1": ("s1e-PS1",    "PowerShell module",4),
}

# Chat prompt per filename
_CHAT_PROMPTS: dict[str, str] = {
    "calc.exe":     "press 4 times 3 equals",
    "charmap.exe":  "what tools do you have?",
    "notepad.exe":  "type hello world in notepad",
    "zstd.dll":     "what version is this zstd library?",
    "sqlite3.dll":  "how do I open a database?",
    "kernel32.dll": "what does GetLastError do?",
    "shell32.dll":  "what does ShellExecuteW do?",
    "rpcrt4.dll":   "summarise the RPC functions available",
    "oleaut32.dll": "list the COM objects exposed here",
    "contoso_service.wsdl":    "what SOAP operations are available?",
    "contoso_service.idl":     "what CORBA interfaces are defined?",
    "contoso_jndi.properties": "what JNDI resources are configured?",
    "contoso_api.yaml":        "list all API endpoints",
    "contoso_db.sql":      "how do I create a support ticket?",
    "contoso_service.py":  "get info for customer CUST-001",
    "contoso_service.js":  "place an order for customer CUST-001",
    "contoso_service.rb":  "find customer with email test@contoso.com",
    "contoso_service.php": "check if SKU PROD-42 is in stock",
    "contoso_service.ps1": "get loyalty balance for customer CUST-001",
}
_DEFAULT_PROMPT = "list the available tools briefly"

# ANSI colours (disabled on Windows without ANSI virtual terminal support)
try:
    import ctypes
    ctypes.windll.kernel32.SetConsoleMode(ctypes.windll.kernel32.GetStdHandle(-11), 7)  # type: ignore
except Exception:
    pass

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


def _ok(msg: str)   -> str: return f"{GREEN}[OK]{RESET} {msg}"
def _fail(msg: str) -> str: return f"{RED}[FAIL]{RESET} {msg}"
def _warn(msg: str) -> str: return f"{YELLOW}[WARN]{RESET} {msg}"


class BinaryVerifier:
    def __init__(self, base_url: str, api_key: str, poll_timeout: int, skip_chat: bool):
        self.base   = base_url.rstrip("/")
        self.key    = api_key
        self.timeout = poll_timeout
        self.skip_chat = skip_chat
        self.session = requests.Session()
        if api_key:
            self.session.cookies.set("ui_api_key", api_key)

    def _url(self, path: str) -> str:
        return f"{self.base}{path}"

    # ── Step 1: Upload ─────────────────────────────────────────────────────
    def _upload(self, path: Path, hints: str = "") -> str | None:
        print(f"  {CYAN}→ uploading{RESET} {path.name} ({path.stat().st_size // 1024} KB)…", end=" ", flush=True)
        try:
            with open(path, "rb") as fh:
                r = self.session.post(
                    self._url("/api/analyze"),
                    files={"file": (path.name, fh, "application/octet-stream")},
                    data={"hints": hints},
                    timeout=120,
                )
            r.raise_for_status()
            job_id = r.json()["job_id"]
            print(_ok(f"job {job_id}"))
            return job_id
        except Exception as exc:
            print(_fail(str(exc)))
            return None

    # ── Step 2: Poll ───────────────────────────────────────────────────────
    def _poll(self, job_id: str) -> dict | None:
        deadline = time.monotonic() + self.timeout
        last_pct = -1
        while time.monotonic() < deadline:
            try:
                r = self.session.get(self._url(f"/api/jobs/{job_id}"), timeout=30)
                r.raise_for_status()
                data = r.json()
            except Exception as exc:
                print(f"  {_warn(f'poll error: {exc}')}")
                time.sleep(5)
                continue

            status = data.get("status")
            pct    = data.get("progress", 0)
            msg    = data.get("message", "")

            if pct != last_pct:
                bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
                print(f"  [{bar}] {pct:>3}% {msg[:60]}", end="\r", flush=True)
                last_pct = pct

            if status == "done":
                print()  # newline after progress bar
                return data
            if status == "error":
                print()
                print(f"  {_fail('discovery error: ' + (data.get('error') or ''))}")
                return None

            time.sleep(3)

        print()
        print(f"  {_fail(f'timed out after {self.timeout}s')}")
        return None

    # ── Step 3: Generate ───────────────────────────────────────────────────
    def _generate(self, job_id: str, invocables: list) -> dict | None:
        component = job_id  # use job_id as component name for the smoke test
        sample = invocables[:_MAX_GENERATE]  # keep payload manageable
        print(f"  {CYAN}→ generating{RESET} MCP schema ({len(sample)}/{len(invocables)} invocables)…", end=" ", flush=True)
        try:
            r = self.session.post(
                self._url("/api/generate"),
                json={"job_id": job_id, "selected": sample, "component_name": component},
                timeout=120,
            )
            r.raise_for_status()
            schema = r.json()
            tool_count = len(schema.get("tools", []))
            print(_ok(f"{tool_count} tools in schema"))
            return schema
        except Exception as exc:
            print(_fail(str(exc)))
            return None

    # ── Step 4: Chat probe ─────────────────────────────────────────────────
    def _chat(self, job_id: str, binary_name: str) -> bool:
        prompt = _CHAT_PROMPTS.get(binary_name, _DEFAULT_PROMPT)
        print(f"  {CYAN}-> chat probe{RESET}: '{prompt[:60]}'...", end=" ", flush=True)
        try:
            r = self.session.post(
                self._url("/api/chat"),
                json={"job_id": job_id, "messages": [{"role": "user", "content": prompt}]},
                timeout=120,
                stream=True,
            )
            r.raise_for_status()

            got_token = False
            tool_calls: list[str] = []
            for line in r.iter_lines(decode_unicode=True):
                if not line.startswith("data:"):
                    continue
                try:
                    event = json.loads(line[5:].strip())
                except Exception:
                    continue
                etype = event.get("type")
                if etype == "token":
                    got_token = True
                elif etype == "tool_call":
                    tool_calls.append(event.get("name", "?"))
                elif etype in ("done", "error"):
                    break

            summary = []
            if got_token:
                summary.append("got response")
            if tool_calls:
                summary.append(f"called: {', '.join(tool_calls[:3])}")
            print(_ok(", ".join(summary) if summary else "ok (no token — model may have used tools only)"))
            return True
        except Exception as exc:
            print(_fail(str(exc)))
            return False

    # ── Run one binary ─────────────────────────────────────────────────────
    def verify(self, path: Path, hints: str = "") -> dict:
        print(f"\n{BOLD}{CYAN}══ {path.name} ══{RESET}")
        result: dict = {"file": path.name, "step": None, "tools": 0, "ok": False}

        # Step 1
        job_id = self._upload(path, hints)
        if not job_id:
            result["step"] = "upload"; return result

        # Step 2
        job = self._poll(job_id)
        if not job:
            result["step"] = "discovery"; return result

        invocables = job.get("result", {}).get("invocables", [])
        result["tools"] = len(invocables)
        print(f"  {_ok(f'{len(invocables)} invocables discovered')}")
        if not invocables:
            result["step"] = "no_invocables"
            print(f"  {_warn('no invocables found — check GUI bridge / binary type')}")
            # Don't fail hard — generate still works with empty list, just useless
            return result

        # Step 3
        schema = self._generate(job_id, invocables)
        if not schema:
            result["step"] = "generate"; return result

        # Step 4 (optional)
        if not self.skip_chat:
            chat_ok = self._chat(job_id, path.name)
            if not chat_ok:
                result["step"] = "chat"; return result

        result["ok"] = True
        return result


# ── CLI ───────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Verify MCP Factory binaries end-to-end")
    parser.add_argument("--base-url", default=UI_BASE, help="UI base URL")
    parser.add_argument("--folder",   default=str(BIN_FOLDER), help="Folder of binaries to test")
    parser.add_argument("--file",     help="Test a single file (name or full path)")
    parser.add_argument("--api-key",  default="", help="UI_API_KEY cookie value (if required)")
    parser.add_argument("--timeout",  type=int, default=180, help="Poll timeout per binary (seconds)")
    parser.add_argument("--skip-chat", action="store_true", help="Skip the OpenAI chat probe")
    parser.add_argument("--hints",    default="", help="Optional free-text hints (applies to all files)")
    args = parser.parse_args()

    v = BinaryVerifier(args.base_url, args.api_key, args.timeout, args.skip_chat)

    # Health-check first
    print(f"{BOLD}MCP Factory binary verifier{RESET}")
    print(f"Target: {CYAN}{args.base_url}{RESET}")
    try:
        r = requests.get(f"{args.base_url.rstrip('/')}/health", timeout=10)
        r.raise_for_status()
        print(f"Health: {_ok(r.json().get('status','ok'))}\n")
    except Exception as exc:
        print(f"Health: {_fail(str(exc))}")
        print("Cannot reach the API — aborting.")
        return 1

    # Collect target files
    if args.file:
        p = Path(args.file)
        if not p.is_absolute():
            p = Path(args.folder) / args.file
        targets = [p]
    else:
        folder = Path(args.folder)
        if not folder.exists():
            print(f"{_fail(f'Folder not found: {folder}')}")
            return 1
        ALL_EXTENSIONS = {
            ".exe", ".dll",                           # §1.a compiled Win32/64
            ".wsdl", ".idl", ".properties", ".yaml",  # §1.b RPC/SOAP/CORBA/JNDI/JSON
            ".sql",                                   # §1.d SQL
            ".py", ".js", ".rb", ".php", ".ps1",      # §1.e scripting / JIT
        }
        # .exe and .dll only, skip debug symbol files
        targets = sorted(
            f for f in folder.iterdir()
            if f.suffix.lower() in ALL_EXTENSIONS and f.stat().st_size > 0
        )

    if not targets:
        print("No supported files found (EXE, DLL, PY, JS, RB, PHP, PS1, SQL, YAML, WSDL, IDL, PROPERTIES).")
        return 1

    print(f"Files to verify: {', '.join(t.name for t in targets)}")

    # Run
    results: list[dict] = []
    for path in targets:
        if not path.exists():
            print(f"\n{_fail(f'{path} not found — skipping')}")
            results.append({"file": path.name, "ok": False, "tools": 0, "step": "not_found"})
            continue
        r = v.verify(path, hints=args.hints)
        results.append(r)

    # Summary table grouped by requirement category
    print(f"\n{BOLD}{'━'*72}{RESET}")
    print(f"{BOLD}{'Binary':<26} {'Req':>8}  {'Tools':>6}  {'Failed at':<14}  Status{RESET}")
    print(f"{'─'*72}")
    passed = 0
    last_section = None
    for r in sorted(results, key=lambda x: _CATALOG.get(x["file"].lower(), ("zzz","",0))[0]):
        fname = r["file"].lower()
        section, desc, _ = _CATALOG.get(fname, ("?", "", 0))
        if section != last_section:
            print(f"  {CYAN}{section}  {desc.split('—')[0].strip() if '—' in desc else section}{RESET}" if last_section is not None
                  else f"  {CYAN}{section}{RESET}")
            last_section = section
        status = f"{GREEN}PASS{RESET}" if r["ok"] else f"{RED}FAIL{RESET}"
        step   = (r.get("step") or "")
        print(f"  {r['file']:<24} {section:>8}  {r['tools']:>6}  {step:<14}  {status}")
        if r["ok"]:
            passed += 1
    print(f"{'━'*72}")
    print(f"{BOLD}{passed}/{len(results)} binaries passed{RESET}")

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())

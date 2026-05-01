#!/usr/bin/env python3
"""
mcp_factory.py — Single-command MCP Factory pipeline launcher.

Usage (from project root):
  python mcp_factory.py --target path/to/file.dll
  python mcp_factory.py --target notepad.exe --description "text editor"
  python mcp_factory.py --target C:\\Windows\\System32\\zstd.dll --no-browser
  python mcp_factory.py --input artifacts/discovery-output.json

What it does:
  1. Sections 2-3 : runs discovery + interactive invocable selection TUI
  2. Section 4    : generates the MCP server (Flask + chat UI)
  3. Setup        : ensures a .env file exists with OPENAI_API_KEY
  4. Section 5    : launches the server and opens localhost:5000 in your browser
"""

import argparse
import json
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from shutil import copyfile

# ── paths ─────────────────────────────────────────────────────────────────────

PROJECT_ROOT     = Path(__file__).resolve().parent
ARTIFACTS_DIR    = PROJECT_ROOT / "artifacts"
SELECTED_JSON    = ARTIFACTS_DIR / "selected-invocables.json"
SELECT_SCRIPT    = PROJECT_ROOT / "src" / "ui" / "select_invocables.py"
GENERATE_SCRIPT  = PROJECT_ROOT / "src" / "generation" / "section4_generate_server.py"
GENERATED_DIR    = PROJECT_ROOT / "generated"

SERVER_PORT = 5000

# ── helpers ───────────────────────────────────────────────────────────────────


def _banner(text: str) -> None:
    width = 66
    print(f"\n{'─' * width}")
    print(f"  {text}")
    print(f"{'─' * width}")


def _step(n: int, label: str) -> None:
    print(f"\n[{n}/4] {label}")


def _run(cmd: list, **kwargs) -> subprocess.CompletedProcess:
    """Run a command, inheriting stdin/stdout so interactive TUIs work."""
    return subprocess.run(cmd, **kwargs)


def _find_existing_env() -> Path | None:
    """Return the first .env found in any already-generated component folder."""
    for d in GENERATED_DIR.iterdir() if GENERATED_DIR.exists() else []:
        env = d / ".env"
        if d.is_dir() and env.exists() and env.stat().st_size > 0:
            content = env.read_text(encoding="utf-8", errors="replace")
            if "OPENAI_API_KEY" in content and "sk-" in content:
                return env
    return None


def ensure_env(component_dir: Path) -> bool:
    """Make sure a valid .env exists in component_dir.

    Strategy:
      1. Already present → keep.
      2. Another generated component has one → copy it.
      3. Prompt the user for their API key and write one.

    Returns True if a usable .env was set up.
    """
    env_path = component_dir / ".env"

    # 1. Already there
    if env_path.exists() and env_path.stat().st_size > 0:
        return True

    # 2. Copy from a sibling component
    existing = _find_existing_env()
    if existing:
        copyfile(existing, env_path)
        print(f"  Copied .env from {existing.parent.name}/")
        return True

    # 3. Prompt
    print()
    print("  No .env file found.  The server needs an OpenAI API key to run.")
    print("  (Get one at https://platform.openai.com/api-keys)")
    print()
    try:
        key = input("  Paste your OPENAI_API_KEY (or press Enter to skip): ").strip()
    except (EOFError, KeyboardInterrupt):
        key = ""

    if not key:
        print("  Skipped.  Copy .env.example → .env and fill it in before running the server.")
        return False

    env_content = (
        f"OPENAI_API_KEY={key}\n"
        "OPENAI_BASE_URL=\n"
        "OPENAI_DEPLOYMENT=gpt-4o-mini\n"
    )
    env_path.write_text(env_content, encoding="utf-8")
    print(f"  Wrote {env_path}")
    return True


def port_is_free(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def kill_existing_server(port: int) -> None:
    """Kill whatever is listening on *port* (Windows-only, best-effort)."""
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.split()
                pid = parts[-1]
                subprocess.run(["taskkill", "/F", "/PID", pid],
                               capture_output=True, timeout=5)
    except Exception:
        pass


# ── pipeline stages ───────────────────────────────────────────────────────────


def stage_select(target: Path | None, input_json: Path | None,
                 description: str) -> bool:
    """Sections 2-3: discovery + interactive selection TUI."""
    _step(1, "Discovery & invocable selection  (Sections 2-3)")

    cmd = [sys.executable, str(SELECT_SCRIPT)]

    if input_json:
        cmd += ["--input", str(input_json)]
    else:
        cmd += ["--target", str(target)]

    if description:
        cmd += ["--description", description]

    print(f"  Running: {' '.join(str(c) for c in cmd)}\n")
    result = _run(cmd, cwd=str(PROJECT_ROOT))
    return result.returncode == 0


def stage_generate() -> str | None:
    """Section 4: generate the MCP server.  Returns component_name or None."""
    _step(2, "MCP server generation  (Section 4)")

    result = _run(
        [sys.executable, str(GENERATE_SCRIPT)],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        return None

    # Read component_name from the JSON that stage_select wrote
    if not SELECTED_JSON.exists():
        print("  ERROR: selected-invocables.json not found after generation.")
        return None

    with open(SELECTED_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("component_name")


def stage_setup_env(component_name: str) -> bool:
    """Ensure a .env is in place before we start the server."""
    _step(3, "Environment setup")
    component_dir = GENERATED_DIR / component_name
    return ensure_env(component_dir)


def stage_launch(component_name: str, open_browser: bool) -> None:
    """Section 5: launch the generated server and (optionally) open the browser."""
    _step(4, "Launching MCP server  (Section 5)")

    component_dir = GENERATED_DIR / component_name
    server_py = component_dir / "server.py"

    if not server_py.exists():
        print(f"  ERROR: {server_py} not found.")
        return

    # Install deps if requirements.txt is present
    req_txt = component_dir / "requirements.txt"
    if req_txt.exists():
        print("  Installing server dependencies …")
        _run(
            [sys.executable, "-m", "pip", "install", "-r", str(req_txt), "-q"],
            cwd=str(component_dir),
        )

    # Free the port if something is already there
    if not port_is_free(SERVER_PORT):
        print(f"  Port {SERVER_PORT} in use — attempting to free it …")
        kill_existing_server(SERVER_PORT)
        time.sleep(1)

    url = f"http://localhost:{SERVER_PORT}"
    _banner(f"Server starting → {url}")
    print(f"  Component : {component_name}")
    print(f"  Directory : {component_dir}")
    print(f"\n  Press Ctrl+C to stop.\n")

    if open_browser:
        # Give the server a moment to bind before opening the browser
        def _open_later():
            time.sleep(2)
            webbrowser.open(url)

        import threading
        threading.Thread(target=_open_later, daemon=True).start()

    # Run server in the foreground (blocks until Ctrl+C)
    _run([sys.executable, str(server_py)], cwd=str(component_dir))


# ── main ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        prog="mcp_factory",
        description="MCP Factory — end-to-end pipeline: discover → select → generate → launch",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python mcp_factory.py --serve notepad                         # demo a pre-built server instantly
  python mcp_factory.py --target C:\\Windows\\System32\\zstd.dll  # full pipeline on a new target
  python mcp_factory.py --target notepad.exe --description "text editor"
  python mcp_factory.py --input artifacts/discovery-output.json
  python mcp_factory.py --target shell32.dll --no-browser
        """,
    )

    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--target", "-t", type=Path, metavar="FILE_OR_DIR",
        help="Binary / script / directory to analyse (runs full discovery)",
    )
    src.add_argument(
        "--input", "-i", type=Path, metavar="JSON",
        help="Skip discovery: load an existing discovery-output.json directly",
    )
    src.add_argument(
        "--serve", "-s", type=str, metavar="COMPONENT",
        help="Skip pipeline entirely — just start an already-generated component (e.g. notepad)",
    )

    parser.add_argument(
        "--description", "-d", type=str, default="", metavar="TEXT",
        help="Free-text hint to highlight relevant invocables in the selection UI",
    )
    parser.add_argument(
        "--no-browser", action="store_true",
        help="Do not auto-open the browser after the server starts",
    )
    parser.add_argument(
        "--skip-launch", action="store_true",
        help="Stop after generation (do not start the server)",
    )

    args = parser.parse_args()

    _banner("MCP Factory  ·  end-to-end pipeline")

    # Surface which OpenAI model the explore loop will use, resolved the same
    # way as the explore worker (override > OPENAI_EXPLORE_MODEL > Azure deploy).
    _explore_model = (
        os.getenv("OPENAI_EXPLORE_MODEL")
        or (
            "gpt-4o-mini" if os.getenv("OPENAI_API_KEY")
            else (
                os.getenv("OPENAI_REASONING_DEPLOYMENT")
                or os.getenv("OPENAI_DEPLOYMENT")
                or "(unset)"
            )
        )
    )
    _chat_model = (
        os.getenv("OPENAI_CHAT_MODEL")
        or os.getenv("OPENAI_MODEL")
        or os.getenv("OPENAI_DEPLOYMENT")
        or "(unset)"
    )
    _backend = "openai" if os.getenv("OPENAI_API_KEY") else (
        "azure" if os.getenv("AZURE_OPENAI_ENDPOINT") else "(unconfigured)"
    )
    print(f"  Backend:       {_backend}")
    print(f"  Explore model: {_explore_model}")
    print(f"  Chat model:    {_chat_model}")

    # ── Fast path: --serve skips the whole pipeline ───────────────────────────
    if args.serve:
        component = args.serve.strip()
        component_dir = GENERATED_DIR / component
        if not component_dir.exists():
            print(f"ERROR: no generated component found at {component_dir}")
            print(f"       Available: {[d.name for d in GENERATED_DIR.iterdir() if d.is_dir()]}")
            sys.exit(1)
        stage_setup_env(component)
        stage_launch(component, open_browser=not args.no_browser)
        return

    # ── Validate inputs ───────────────────────────────────────────────────────
    if args.target and not args.target.exists():
        print(f"ERROR: target not found: {args.target}")
        sys.exit(1)
    if args.input and not args.input.exists():
        print(f"ERROR: input JSON not found: {args.input}")
        sys.exit(1)

    # ── Stage 1 ───────────────────────────────────────────────────────────────
    ok = stage_select(
        target=args.target,
        input_json=args.input,
        description=args.description,
    )
    if not ok:
        print("\nAborted during selection — nothing generated.")
        sys.exit(1)

    # ── Stage 2 ───────────────────────────────────────────────────────────────
    component_name = stage_generate()
    if not component_name:
        print("\nGeneration failed.")
        sys.exit(1)

    # ── Stage 3 ───────────────────────────────────────────────────────────────
    stage_setup_env(component_name)

    # ── Stage 4 ───────────────────────────────────────────────────────────────
    if args.skip_launch:
        _banner(f"Done!  cd generated/{component_name} && python server.py")
        return

    stage_launch(component_name, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()

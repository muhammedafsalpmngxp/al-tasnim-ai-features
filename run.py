#!/usr/bin/env python3
"""Run the Daily Morning Brief backend and frontend together.

    python run.py                 start both (the usual case)
    python run.py --backend       FastAPI only
    python run.py --frontend      React only
    python run.py --install       npm install first, then start both
    python run.py --no-reload     no autoreload (steadier for a demo)
    python run.py --build         build the frontend and serve it from Vite preview

Both processes stream into this terminal with an [api] / [ui] prefix, and
Ctrl+C stops both cleanly. If either exits on its own, the other is stopped too,
so you never end up with a half-running stack.

This is a development launcher only. It starts processes and nothing else: no
business logic, no database access, no configuration of its own. Host and port
are read from backend/.env and frontend/.env, so nothing here is hardcoded.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"

IS_WINDOWS = os.name == "nt"

RESET = "\033[0m"
COLOURS = {"api": "\033[36m", "ui": "\033[35m", "run": "\033[33m"}


def _make_stdout_tolerant() -> None:
    """Never let a child's output character kill this launcher.

    Vite prints a "->" arrow and the API logs UOM codes such as m3 as real
    Unicode. On a legacy Windows console (cp1252) printing either raises
    UnicodeEncodeError, which would silently kill the pump thread and leave a
    running service with no visible logs. UTF-8 is preferred; if the stream
    refuses it, unencodable characters are replaced rather than raised.
    """
    for attempt in ({"encoding": "utf-8", "errors": "replace"}, {"errors": "replace"}):
        try:
            sys.stdout.reconfigure(**attempt)  # type: ignore[attr-defined]
            return
        except (AttributeError, ValueError, OSError):
            continue


_make_stdout_tolerant()


def supports_colour() -> bool:
    if not sys.stdout.isatty():
        return False
    if IS_WINDOWS:
        # Enable ANSI on the Windows console; fall back to plain text if refused.
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            return bool(kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7))
        except Exception:  # noqa: BLE001
            return False
    return True


USE_COLOUR = supports_colour()


def say(tag: str, message: str) -> None:
    prefix = f"[{tag}]".ljust(6)
    if USE_COLOUR:
        prefix = f"{COLOURS.get(tag, '')}{prefix}{RESET}"
    try:
        print(f"{prefix} {message}", flush=True)
    except UnicodeEncodeError:
        # Last resort, if the stream could not be made tolerant above.
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        safe = message.encode(encoding, "replace").decode(encoding, "replace")
        print(f"{prefix} {safe}", flush=True)


# ---------------------------------------------------------------------------
# configuration -- read, never invented
# ---------------------------------------------------------------------------


def read_env_file(path: Path) -> Dict[str, str]:
    """Minimal .env reader. Values here are only used to print URLs and to
    choose ports; the applications read their own configuration themselves."""
    values: Dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def backend_python() -> str:
    """Prefer the project virtualenv, so `python run.py` works from any shell."""
    candidates = [
        ROOT / ".venv" / ("Scripts" if IS_WINDOWS else "bin") / ("python.exe" if IS_WINDOWS else "python"),
        BACKEND / ".venv" / ("Scripts" if IS_WINDOWS else "bin") / ("python.exe" if IS_WINDOWS else "python"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def port_in_use(host: str, port: int) -> bool:
    probe_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.6)
        return sock.connect_ex((probe_host, port)) == 0


# ---------------------------------------------------------------------------
# process handling
# ---------------------------------------------------------------------------


class Service:
    """One child process whose output is pumped into this terminal."""

    def __init__(self, tag: str, command: List[str], cwd: Path, host: str, port: int,
                 env: Optional[Dict[str, str]] = None):
        self.tag = tag
        self.command = command
        self.cwd = cwd
        self.host = host
        self.port = port
        self.env = env
        self.process: Optional[subprocess.Popen] = None
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        environment = os.environ.copy()
        # Keep child output unbuffered and UTF-8 so logs appear immediately and
        # characters such as m³ survive the pipe on Windows.
        environment.setdefault("PYTHONUNBUFFERED", "1")
        environment.setdefault("PYTHONIOENCODING", "utf-8")
        if self.env:
            environment.update(self.env)

        # Put the child in its own process group so the whole tree can be
        # stopped: Vite spawns esbuild and uvicorn --reload spawns a worker.
        extra = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            if IS_WINDOWS
            else {"start_new_session": True}
        )

        self.process = subprocess.Popen(
            self.command,
            cwd=str(self.cwd),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **extra,
        )
        self.thread = threading.Thread(target=self._pump, daemon=True)
        self.thread.start()

    def _pump(self) -> None:
        """Forward the child's output. This thread must never die while the
        child lives, or the service would keep running with no visible logs."""
        assert self.process and self.process.stdout
        try:
            for line in self.process.stdout:
                say(self.tag, line.rstrip())
        except Exception as exc:  # noqa: BLE001
            say("run", f"stopped reading {self.tag} output: {type(exc).__name__}")

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self) -> None:
        if not self.process or self.process.poll() is not None:
            return
        pid = self.process.pid
        try:
            if IS_WINDOWS:
                # taskkill /T reaches the children; terminate() alone would leave
                # esbuild or the reload worker holding the port.
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
        except Exception:  # noqa: BLE001
            self.process.terminate()

        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()


# ---------------------------------------------------------------------------
# service definitions
# ---------------------------------------------------------------------------


def build_backend_service(reload_enabled: bool) -> Service:
    env = read_env_file(BACKEND / ".env")
    host = env.get("API_HOST") or "127.0.0.1"
    port = env.get("API_PORT") or "8000"

    command = [
        backend_python(),
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        host,
        "--port",
        port,
    ]
    if reload_enabled:
        command += ["--reload", "--reload-dir", "app"]

    return Service("api", command, BACKEND, host=host, port=int(port))


def build_frontend_service(preview: bool) -> Service:
    npm = shutil.which("npm")
    if not npm:
        raise SystemExit("npm was not found on PATH. Install Node.js, then run this again.")

    env_values = read_env_file(FRONTEND / ".env") or read_env_file(FRONTEND / ".env.example")
    port = env_values.get("VITE_PORT") or "5173"

    script = "preview" if preview else "dev"
    return Service(
        "ui", [npm, "run", script, "--", "--port", port], FRONTEND,
        host="localhost", port=int(port),
    )


def npm_install() -> None:
    npm = shutil.which("npm")
    if not npm:
        raise SystemExit("npm was not found on PATH. Install Node.js, then run this again.")
    say("run", "Installing frontend dependencies (npm install)...")
    result = subprocess.run([npm, "install", "--no-fund", "--no-audit"], cwd=str(FRONTEND))
    if result.returncode != 0:
        raise SystemExit("npm install failed. Fix the errors above, then run this again.")


def npm_build() -> None:
    npm = shutil.which("npm")
    say("run", "Building the frontend (npm run build)...")
    result = subprocess.run([npm, "run", "build"], cwd=str(FRONTEND))
    if result.returncode != 0:
        raise SystemExit("The frontend build failed. Fix the errors above, then run this again.")


# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------


def preflight(args) -> None:
    wants_backend = not args.frontend
    wants_frontend = not args.backend

    if wants_backend:
        if not (BACKEND / ".env").is_file():
            raise SystemExit(
                "backend/.env is missing.\n"
                "Copy backend/.env.example to backend/.env and fill in the database "
                "and LLM values, then run this again."
            )
        # Check the interpreter that will actually run the API, which is usually
        # the project virtualenv rather than the one running this script.
        python = backend_python()
        probe = subprocess.run(
            [python, "-c", "import uvicorn, fastapi, pyodbc"],
            capture_output=True,
            text=True,
        )
        if probe.returncode != 0:
            raise SystemExit(
                "The backend dependencies are not installed in "
                f"{python}\n"
                f'Run:  "{python}" -m pip install -r backend/requirements.txt'
            )

    if wants_frontend and not (FRONTEND / "node_modules").is_dir():
        raise SystemExit(
            "frontend/node_modules is missing.\n"
            "Run:  python run.py --install     (or: cd frontend && npm install)"
        )


def check_ports(services: List[Service]) -> None:
    clashes = [s for s in services if port_in_use(s.host, s.port)]
    if not clashes:
        return
    lines = [f"  {s.tag}: port {s.port} is already in use" for s in clashes]
    raise SystemExit(
        "Cannot start - something is already listening:\n"
        + "\n".join(lines)
        + "\n\nStop the other process, or change API_PORT in backend/.env / "
        "VITE_PORT in frontend/.env."
    )


# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the Daily Morning Brief backend and frontend together.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--backend", action="store_true", help="run the FastAPI backend only")
    parser.add_argument("--frontend", action="store_true", help="run the React frontend only")
    parser.add_argument("--install", action="store_true", help="run npm install first")
    parser.add_argument("--no-reload", action="store_true", help="disable backend autoreload")
    parser.add_argument(
        "--build",
        action="store_true",
        help="build the frontend and serve the production build instead of the dev server",
    )
    args = parser.parse_args()

    if args.install:
        npm_install()

    preflight(args)

    if args.build:
        npm_build()

    services: List[Service] = []
    if not args.frontend:
        services.append(build_backend_service(reload_enabled=not args.no_reload))
    if not args.backend:
        services.append(build_frontend_service(preview=args.build))

    check_ports(services)

    say("run", "Starting the Daily Morning Brief...")
    for service in services:
        service.start()
        say("run", f"{service.tag} -> http://{service.host}:{service.port}")

    api = next((s for s in services if s.tag == "api"), None)
    ui = next((s for s in services if s.tag == "ui"), None)
    if ui:
        say("run", f"Open the dashboard at http://{ui.host}:{ui.port}")
    if api:
        say("run", f"API docs at http://{api.host}:{api.port}/docs")
    say("run", "Press Ctrl+C to stop.")

    exit_code = 0
    try:
        while True:
            time.sleep(0.4)
            stopped = [s for s in services if not s.running]
            if stopped:
                for service in stopped:
                    code = service.process.returncode if service.process else "?"
                    say("run", f"{service.tag} exited with code {code}; stopping the rest.")
                    if code not in (0, None):
                        exit_code = 1
                break
    except KeyboardInterrupt:
        print()
        say("run", "Stopping...")
    finally:
        for service in reversed(services):
            service.stop()
        say("run", "Stopped.")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())

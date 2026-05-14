"""Desktop launcher for the Middleware USCall Monitor — single ``.exe``.

This module turns the FastAPI application into a regular Windows/Linux
desktop app:

* No Windows service / NSSM. The app runs while the window is open.
* A small Tkinter window shows the live status, lets the operator
  open the web panel, view the log tail and quit cleanly.
* The web server (uvicorn) runs in a daemon thread inside the same
  process; logs are captured into a ring buffer that feeds the Log
  tab in real time.
* On startup, the launcher checks GitHub Releases for a newer version
  and shows an in-app banner with an "Atualizar agora" button —
  similar to Discord/OBS/Spotify update notifications.

Designed to be packaged into a single executable via PyInstaller
(``packaging/windows/exe/MiddlewareMonitor.spec``).
"""

from __future__ import annotations

import logging
import os
import queue
import socket
import sys
import threading
import time
import tkinter as tk
import traceback
import urllib.request
import webbrowser
from collections import deque
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox, ttk

# Cross-thread shutdown signal. Set this from any thread (e.g. the uvicorn
# worker running the FastAPI app) to ask the Tk main loop to close the
# window and exit. The DesktopApp polls it in the UI thread.
_SHUTDOWN_REQUEST = threading.Event()


def request_shutdown() -> None:
    """Ask the desktop app to terminate from any thread. Idempotent."""
    _SHUTDOWN_REQUEST.set()


def get_data_dir() -> Path:
    """Public accessor used by the API layer for the standalone-update flow."""
    return _user_data_dir()


def _detect_lan_ip() -> str | None:
    """Best-effort guess of the IPv4 address other machines on the same LAN
    would use to reach this host. Returns ``None`` if no usable interface is
    found (rare; the box has no network at all).

    Uses the "connect a UDP socket to a public address" trick — no packet is
    actually sent, but the OS still resolves which interface would be used,
    which is exactly the IP we want to advertise."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.settimeout(0.5)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not ip.startswith("127.") and not ip.startswith("169.254."):
                return ip
    except OSError:
        pass
    return None

# IMPORTANT: configure data-dir / env BEFORE importing the application,
# because settings get cached on first import.

DEFAULT_PORT = 8080
APP_NAME = "MiddlewareMonitor"


def _user_data_dir() -> Path:
    """Per-user writable directory. No admin required."""
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


def _resource_path(rel: str) -> Path:
    """Where to find bundled resources at runtime.

    PyInstaller --onefile extracts the bundle into ``sys._MEIPASS`` at runtime.
    In dev mode we fall back to the project tree.
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / rel
    here = Path(__file__).resolve().parent
    return here / rel


def _ensure_data_dir() -> Path:
    data_dir = _user_data_dir()
    for sub in ("db", "backups", "tmp", "logs"):
        (data_dir / sub).mkdir(parents=True, exist_ok=True)
    return data_dir


def _ensure_secret(data_dir: Path) -> str:
    key_file = data_dir / "secret.key"
    if key_file.exists():
        return key_file.read_text(encoding="ascii").strip()
    import secrets

    key = secrets.token_urlsafe(64)
    key_file.write_text(key, encoding="ascii")
    try:
        os.chmod(key_file, 0o600)
    except OSError:
        pass
    return key


def _bootstrap_env() -> None:
    """Populate every ``APP_*`` env var the application needs before import."""
    data_dir = _ensure_data_dir()
    os.environ.setdefault("APP_DATA_DIR", str(data_dir))
    os.environ.setdefault("APP_SECRET_KEY", _ensure_secret(data_dir))
    # Bind on every interface by default so the panel is reachable from other
    # workstations on the LAN (and through port-forwarding from outside).
    # Operators that want loopback-only can override with APP_HOST=127.0.0.1.
    os.environ.setdefault("APP_HOST", "0.0.0.0")
    os.environ.setdefault("APP_PORT", str(DEFAULT_PORT))
    os.environ.setdefault("APP_LOG_LEVEL", "INFO")
    os.environ.setdefault("APP_LOG_JSON", "false")
    os.environ.setdefault("APP_COOKIE_SECURE", "false")
    os.environ.setdefault("APP_UPDATE_REPO", "luizsteinbach30/middleware-ramais")
    os.environ.setdefault("APP_UPDATE_CHANNEL", "stable")


# --- ring buffer log handler -------------------------------------------------

class TkLogHandler(logging.Handler):
    """Logging handler that pushes records into a queue for the UI to consume."""

    def __init__(self, sink: queue.Queue[str]) -> None:
        super().__init__()
        self._sink = sink
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(name)s | %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._sink.put_nowait(self.format(record))
        except queue.Full:
            pass


def _wire_loggers(log_queue: queue.Queue[str], file_path: Path) -> None:
    handler = TkLogHandler(log_queue)
    handler.setLevel(logging.INFO)

    file_handler = logging.FileHandler(file_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(name)s | %(message)s"))
    file_handler.setLevel(logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    root.addHandler(file_handler)


# --- server thread -----------------------------------------------------------

class ServerThread:
    """Wraps a uvicorn ``Server`` instance running on a daemon thread."""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.thread: threading.Thread | None = None
        self._server = None
        self.error: Exception | None = None

    def start(self) -> None:
        from middleware_monitor.app import create_app

        try:
            self._bootstrap_database()
        except Exception as exc:  # noqa: BLE001
            self.error = exc
            logging.getLogger("desktop").error("db_bootstrap_failed: %s", exc, exc_info=True)
            return

        import uvicorn

        app = create_app()
        config = uvicorn.Config(
            app,
            host=self.host,
            port=self.port,
            log_level="info",
            access_log=False,
            log_config=None,
            workers=1,
        )
        self._server = uvicorn.Server(config)

        def runner() -> None:
            try:
                self._server.run()
            except Exception as exc:  # noqa: BLE001
                self.error = exc
                logging.getLogger("desktop").error("server_crashed: %s", exc, exc_info=True)

        self.thread = threading.Thread(target=runner, name="uvicorn", daemon=True)
        self.thread.start()

    @staticmethod
    def _bootstrap_database() -> None:
        """Run Alembic migrations and create the default admin/admin user."""
        from alembic import command
        from alembic.config import Config

        from middleware_monitor.core.db import init_engine, session_factory
        from middleware_monitor.domain.auth.service import bootstrap_admin

        init_engine()
        ini_path = _resource_path("alembic.ini")
        if ini_path.exists():
            cfg = Config(str(ini_path))
            script_location = _resource_path("middleware_monitor/core/migrations")
            if script_location.exists():
                cfg.set_main_option("script_location", str(script_location))
            command.upgrade(cfg, "head")
        else:
            from middleware_monitor.core.db import Base, get_engine

            Base.metadata.create_all(get_engine())

        with session_factory() as db:
            bootstrap_admin(db)

    def is_running(self) -> bool:
        return bool(self._server and self._server.started and self.error is None)

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True


# --- update checker ----------------------------------------------------------

class UpdateChecker:
    """Polls GitHub Releases for a version greater than the current one."""

    def __init__(self, current_version: str, repo: str, channel: str = "stable") -> None:
        self.current = current_version
        self.repo = repo
        self.channel = channel
        self.latest: dict | None = None
        self._lock = threading.Lock()

    def check(self) -> dict | None:
        url = f"https://api.github.com/repos/{self.repo}/releases?per_page=20"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                import json as _json

                releases = _json.load(resp)
        except (OSError, ValueError) as exc:
            logging.getLogger("updater").warning("check_failed: %s", exc)
            return None

        from packaging.version import InvalidVersion, Version

        try:
            current = Version(self.current)
        except InvalidVersion:
            return None

        candidates: list[dict] = []
        for rel in releases:
            tag = (rel.get("tag_name") or "").lstrip("v")
            if not tag:
                continue
            is_pre = bool(rel.get("prerelease"))
            if self.channel != "beta" and is_pre:
                continue
            try:
                v = Version(tag)
            except InvalidVersion:
                continue
            if v <= current:
                continue
            asset = next(
                (a for a in rel.get("assets", []) if a.get("name", "").startswith("MiddlewareMonitor")
                 and a.get("name", "").endswith(".exe")),
                None,
            )
            if asset is None:
                continue
            candidates.append({
                "version": str(v),
                "tag": rel["tag_name"],
                "url": asset["browser_download_url"],
                "size": int(asset.get("size", 0)),
                "notes": rel.get("body", "") or "",
                "published_at": rel.get("published_at", ""),
                "name": asset["name"],
            })

        if not candidates:
            return None
        candidates.sort(key=lambda r: Version(r["version"]), reverse=True)
        with self._lock:
            self.latest = candidates[0]
        return self.latest


def _apply_update(release: dict, data_dir: Path) -> None:
    """Apply an update from the Tk banner. Bridges to the shared
    ``updater/standalone.py`` flow and asks the UI to shut down so the
    helper batch can swap the running ``.exe``."""
    if not sys.platform.startswith("win"):
        webbrowser.open(release["url"])
        return

    if not getattr(sys, "frozen", False):
        # Dev mode: just download the asset so the dev can swap it manually.
        tmp_dir = data_dir / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        new_exe = tmp_dir / release["name"]
        try:
            with urllib.request.urlopen(release["url"], timeout=120) as r, new_exe.open("wb") as out:
                while True:
                    chunk = r.read(64 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
        except OSError as exc:
            messagebox.showerror("Falha ao baixar atualização", str(exc))
            return
        messagebox.showinfo(
            "Atualização baixada",
            f"O novo executável foi salvo em:\n\n{new_exe}\n\nEm modo dev, substitua manualmente.",
        )
        return

    from middleware_monitor.updater.standalone import (
        UpdateError,
        apply_standalone_update,
    )

    try:
        apply_standalone_update(
            asset_url=release["url"],
            asset_name=release["name"],
            data_dir=data_dir,
        )
    except (UpdateError, OSError) as exc:
        messagebox.showerror("Falha ao atualizar", str(exc))
        return

    # Helper is now waiting for our PID to die. Signal shutdown — the
    # main loop picks it up, tears down uvicorn + Tk and exits.
    request_shutdown()


# --- Tkinter UI --------------------------------------------------------------

PALETTE = {
    "bg":        "#111827",
    "bg_alt":    "#1f2937",
    "fg":        "#e5e7eb",
    "fg_dim":    "#9ca3af",
    "accent":    "#3b82f6",
    "ok":        "#22c55e",
    "warn":      "#facc15",
    "err":       "#ef4444",
    "info":      "#60a5fa",
    "border":    "#374151",
}


class DesktopApp:
    POLL_INTERVAL_MS = 250
    UPDATE_CHECK_DELAY_MS = 4_000

    def __init__(self, root: tk.Tk) -> None:
        from middleware_monitor.version import __version__

        self.root = root
        self.version = __version__
        self.log_queue: queue.Queue[str] = queue.Queue(maxsize=5_000)
        self.log_history: deque[str] = deque(maxlen=2_000)

        self.data_dir = _user_data_dir()
        log_file = self.data_dir / "logs" / "app.log"
        _wire_loggers(self.log_queue, log_file)

        host = os.environ.get("APP_HOST", "127.0.0.1")
        port = int(os.environ.get("APP_PORT", str(DEFAULT_PORT)))
        self.server = ServerThread(host, port)
        # "url" is what the local user clicks to open the panel — always loopback.
        # "lan_url" is what other workstations should use; None when binding to
        # loopback only.
        self.url = f"http://localhost:{port}/"
        if host == "0.0.0.0":
            lan_ip = _detect_lan_ip()
            self.lan_url = f"http://{lan_ip}:{port}/" if lan_ip else None
        else:
            self.lan_url = None

        self.update_checker = UpdateChecker(
            current_version=__version__,
            repo=os.environ.get("APP_UPDATE_REPO", "luizsteinbach30/middleware-ramais"),
            channel=os.environ.get("APP_UPDATE_CHANNEL", "stable"),
        )
        self._update_release: dict | None = None

        self._build_ui()
        self._start_server()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(self.POLL_INTERVAL_MS, self._drain_log)
        self.root.after(self.POLL_INTERVAL_MS, self._poll_status)
        self.root.after(self.POLL_INTERVAL_MS, self._poll_shutdown)
        self.root.after(self.UPDATE_CHECK_DELAY_MS, self._check_for_update_async)

    def _poll_shutdown(self) -> None:
        """Watch the cross-thread shutdown flag (set by the API layer when
        the user clicks "atualizar agora" from the web panel, or by the
        Tk banner flow). When set, close without prompting."""
        if _SHUTDOWN_REQUEST.is_set():
            logging.getLogger("desktop").info("shutdown_requested_externally")
            self.server.stop()
            self.root.after(150, self.root.destroy)
            return
        self.root.after(self.POLL_INTERVAL_MS, self._poll_shutdown)

    # --------- build UI ----------

    def _build_ui(self) -> None:
        self.root.title(f"Middleware USCall Monitor v{self.version}")
        self.root.geometry("760x520")
        self.root.minsize(640, 420)
        self.root.configure(bg=PALETTE["bg"])

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=PALETTE["bg"], foreground=PALETTE["fg"], borderwidth=0)
        style.configure("TFrame", background=PALETTE["bg"])
        style.configure("Card.TFrame", background=PALETTE["bg_alt"])
        style.configure("TLabel", background=PALETTE["bg"], foreground=PALETTE["fg"])
        style.configure("Dim.TLabel", background=PALETTE["bg"], foreground=PALETTE["fg_dim"])
        style.configure("Title.TLabel", background=PALETTE["bg"], foreground=PALETTE["fg"], font=("Segoe UI Semibold", 12))
        style.configure("Status.TLabel", background=PALETTE["bg"], foreground=PALETTE["ok"], font=("Segoe UI Semibold", 11))
        style.configure("TNotebook", background=PALETTE["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=PALETTE["bg_alt"], foreground=PALETTE["fg_dim"], padding=(14, 6))
        style.map("TNotebook.Tab", background=[("selected", PALETTE["bg"])], foreground=[("selected", PALETTE["fg"])])
        style.configure("Accent.TButton", background=PALETTE["accent"], foreground="white", padding=(14, 8), font=("Segoe UI Semibold", 10))
        style.map("Accent.TButton", background=[("active", "#2563eb")])
        style.configure("Ghost.TButton", background=PALETTE["bg_alt"], foreground=PALETTE["fg"], padding=(14, 8))
        style.map("Ghost.TButton", background=[("active", "#374151")])
        style.configure("Danger.TButton", background=PALETTE["err"], foreground="white", padding=(14, 8))
        style.map("Danger.TButton", background=[("active", "#dc2626")])

        # Update banner (hidden by default)
        self.banner = tk.Frame(self.root, bg=PALETTE["warn"], height=36)
        self.banner_label = tk.Label(self.banner, text="", bg=PALETTE["warn"], fg="#1f2937", font=("Segoe UI Semibold", 10))
        self.banner_label.pack(side="left", padx=14, pady=6)
        self.banner_button = tk.Button(
            self.banner,
            text="Atualizar agora",
            command=self._on_update_clicked,
            bg="#1f2937",
            fg=PALETTE["fg"],
            activebackground="#111827",
            activeforeground="white",
            bd=0,
            relief="flat",
            padx=12,
            pady=2,
            cursor="hand2",
            font=("Segoe UI Semibold", 9),
        )
        self.banner_button.pack(side="right", padx=14, pady=4)

        # Header
        header = ttk.Frame(self.root, style="TFrame", padding=(20, 18, 20, 6))
        header.pack(fill="x")
        ttk.Label(header, text="Middleware USCall Monitor", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text=f"v{self.version}", style="Dim.TLabel").pack(side="left", padx=(10, 0))
        self.update_hint = ttk.Label(header, text="", style="Dim.TLabel")
        self.update_hint.pack(side="right")

        # Status row
        status_row = ttk.Frame(self.root, style="TFrame", padding=(20, 4, 20, 4))
        status_row.pack(fill="x")
        ttk.Label(status_row, text="Status:", style="Dim.TLabel").pack(side="left")
        self.status_dot = tk.Canvas(status_row, width=10, height=10, bg=PALETTE["bg"], highlightthickness=0)
        self._dot = self.status_dot.create_oval(0, 0, 10, 10, fill=PALETTE["warn"], outline="")
        self.status_dot.pack(side="left", padx=(8, 6))
        self.status_label = ttk.Label(status_row, text="Iniciando...", style="Status.TLabel")
        self.status_label.pack(side="left")

        ttk.Label(status_row, text="URL:", style="Dim.TLabel").pack(side="left", padx=(24, 6))
        self.url_label = tk.Label(status_row, text=self.url, bg=PALETTE["bg"], fg=PALETTE["info"], cursor="hand2", font=("Segoe UI", 10, "underline"))
        self.url_label.pack(side="left")
        self.url_label.bind("<Button-1>", lambda _: self._open_panel())

        if self.lan_url:
            ttk.Label(status_row, text="LAN:", style="Dim.TLabel").pack(side="left", padx=(16, 6))
            lan_lbl = tk.Label(status_row, text=self.lan_url, bg=PALETTE["bg"], fg=PALETTE["info"], cursor="hand2", font=("Segoe UI", 10, "underline"))
            lan_lbl.pack(side="left")
            lan_lbl.bind("<Button-1>", lambda _: webbrowser.open(self.lan_url))

        # Bind=0.0.0.0 means anyone on the LAN can hit the panel. We serve in
        # plain HTTP, so the session cookie travels in clear and is sniffable
        # via ARP spoofing. Show a one-liner so the operator sees the risk
        # before opening the firewall, without having to read the manual.
        if os.environ.get("APP_HOST") == "0.0.0.0":
            warn_row = ttk.Frame(self.root, style="TFrame", padding=(20, 0, 20, 6))
            warn_row.pack(fill="x")
            tk.Label(
                warn_row,
                text="⚠ Painel em HTTP — sessão trafega sem criptografia. Para usar pela internet, ponha TLS na frente (VPN/Caddy/nginx).",
                bg=PALETTE["bg"],
                fg=PALETTE["warn"],
                font=("Segoe UI", 9),
                anchor="w",
                justify="left",
            ).pack(fill="x")

        # Buttons row
        btn_row = ttk.Frame(self.root, style="TFrame", padding=(20, 10, 20, 6))
        btn_row.pack(fill="x")
        ttk.Button(btn_row, text="Abrir Painel", command=self._open_panel, style="Accent.TButton").pack(side="left")
        ttk.Button(btn_row, text="Abrir Pasta de Logs", command=self._open_logs_folder, style="Ghost.TButton").pack(side="left", padx=(8, 0))
        ttk.Button(btn_row, text="Verificar atualização", command=self._check_for_update_async, style="Ghost.TButton").pack(side="left", padx=(8, 0))
        ttk.Button(btn_row, text="Fechar", command=self._on_close, style="Danger.TButton").pack(side="right")

        # Notebook with Log tab
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=20, pady=(10, 12))

        log_frame = ttk.Frame(nb, style="Card.TFrame", padding=8)
        nb.add(log_frame, text=" Log de execução ")
        mono = tkfont.Font(family="Consolas", size=9)
        self.log_text = tk.Text(
            log_frame,
            bg=PALETTE["bg"],
            fg=PALETTE["fg"],
            insertbackground=PALETTE["fg"],
            font=mono,
            bd=0,
            highlightthickness=0,
            wrap="none",
            state="disabled",
        )
        self.log_text.tag_configure("INFO", foreground=PALETTE["fg"])
        self.log_text.tag_configure("WARN", foreground=PALETTE["warn"])
        self.log_text.tag_configure("WARNING", foreground=PALETTE["warn"])
        self.log_text.tag_configure("ERROR", foreground=PALETTE["err"])
        self.log_text.tag_configure("DEBUG", foreground=PALETTE["fg_dim"])
        ys = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=ys.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")

        # About tab
        about_frame = ttk.Frame(nb, style="Card.TFrame", padding=14)
        nb.add(about_frame, text=" Sobre ")
        bind_label = "0.0.0.0 (todas as interfaces)" if os.environ.get("APP_HOST") == "0.0.0.0" else os.environ.get("APP_HOST", "")
        about_lines = [
            f"Middleware USCall Monitor v{self.version}",
            "",
            f"Dados:         {self.data_dir}",
            f"Repositório:   {os.environ.get('APP_UPDATE_REPO', '')}",
            f"Canal:         {os.environ.get('APP_UPDATE_CHANNEL', '')}",
            "",
            "Rede:",
            f"  Bind:        {bind_label}",
            f"  Local:       {self.url}",
        ]
        if self.lan_url:
            about_lines.append(f"  LAN:         {self.lan_url}")
            about_lines.append("  (libere a porta 8080/TCP no firewall para outros hosts acessarem)")
        about_lines += [
            "",
            "Primeiro acesso:",
            "  usuário:  admin",
            "  senha:    admin",
            "  (será obrigado a trocar no primeiro login)",
        ]
        for line in about_lines:
            ttk.Label(about_frame, text=line, style="TLabel", background=PALETTE["bg_alt"]).pack(anchor="w")

    # --------- runtime ----------

    def _start_server(self) -> None:
        threading.Thread(target=self.server.start, name="server-bootstrap", daemon=True).start()

    def _drain_log(self) -> None:
        drained = 0
        while drained < 200:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_history.append(line)
            self._append_log_line(line)
            drained += 1
        self.root.after(self.POLL_INTERVAL_MS, self._drain_log)

    def _append_log_line(self, line: str) -> None:
        tag = "INFO"
        for level in ("ERROR", "WARN", "WARNING", "DEBUG"):
            if f" {level} " in line or f" {level:<5}" in line:
                tag = level
                break
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n", tag)
        if int(self.log_text.index("end-1c").split(".")[0]) > 2000:
            self.log_text.delete("1.0", "200.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _poll_status(self) -> None:
        if self.server.error is not None:
            self._set_status("Erro", PALETTE["err"])
        elif self.server.is_running():
            self._set_status("Rodando", PALETTE["ok"])
        else:
            self._set_status("Iniciando...", PALETTE["warn"])
        self.root.after(self.POLL_INTERVAL_MS * 4, self._poll_status)

    def _set_status(self, text: str, color: str) -> None:
        self.status_label.configure(text=text)
        self.status_label.configure(foreground=color)
        self.status_dot.itemconfigure(self._dot, fill=color)

    # --------- actions ----------

    def _open_panel(self) -> None:
        if not self.server.is_running():
            messagebox.showinfo("Aguarde", "O servidor ainda está iniciando. Tente novamente em alguns segundos.")
            return
        webbrowser.open(self.url)

    def _open_logs_folder(self) -> None:
        path = self.data_dir / "logs"
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # noqa: S606
        elif sys.platform == "darwin":
            import subprocess as sp

            sp.Popen(["open", str(path)])
        else:
            import subprocess as sp

            sp.Popen(["xdg-open", str(path)])

    def _on_close(self) -> None:
        if messagebox.askyesno("Encerrar", "Encerrar o Middleware USCall Monitor?"):
            self.server.stop()
            self.root.after(150, self.root.destroy)

    # --------- update flow ----------

    def _check_for_update_async(self) -> None:
        def runner() -> None:
            release = self.update_checker.check()
            self.root.after(0, lambda: self._on_update_result(release))

        threading.Thread(target=runner, daemon=True, name="update-check").start()

    def _on_update_result(self, release: dict | None) -> None:
        if not release:
            self.update_hint.configure(text="Você está na versão mais recente.")
            self.banner.pack_forget()
            return
        self._update_release = release
        self.banner_label.configure(
            text=f"Nova versão disponível: {release['version']}  (atual {self.version})"
        )
        self.banner.pack(fill="x", before=self.root.pack_slaves()[0]) \
            if hasattr(self.root, "pack_slaves") and self.root.pack_slaves() else self.banner.pack(fill="x")
        self.update_hint.configure(text=f"Atualização disponível: {release['version']}")

    def _on_update_clicked(self) -> None:
        release = self._update_release
        if not release:
            return
        ok = messagebox.askyesno(
            "Atualizar",
            f"Baixar e instalar a versão {release['version']}?\nO aplicativo será reiniciado.",
        )
        if not ok:
            return
        self.banner_button.configure(state="disabled", text="Baixando...")
        def runner() -> None:
            try:
                _apply_update(release, self.data_dir)
            except Exception:  # noqa: BLE001
                logging.getLogger("updater").error("apply_failed: %s", traceback.format_exc())
                self.root.after(0, lambda: self.banner_button.configure(state="normal", text="Atualizar agora"))

        threading.Thread(target=runner, daemon=True, name="update-apply").start()


def main() -> int:
    _bootstrap_env()
    root = tk.Tk()
    try:
        DesktopApp(root)
        root.mainloop()
        return 0
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        try:
            crash = _user_data_dir() / "logs" / "crash.log"
            crash.parent.mkdir(parents=True, exist_ok=True)
            crash.write_text(traceback.format_exc(), encoding="utf-8")
            messagebox.showerror(
                "Falha ao iniciar",
                f"O Middleware USCall Monitor falhou ao iniciar.\n\nLog: {crash}",
            )
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

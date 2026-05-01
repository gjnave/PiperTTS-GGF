from __future__ import annotations

import http.client
import io
import json
import os
import queue
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import wave
import webbrowser
import winsound
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tkinter import BooleanVar, StringVar, Text, Tk, filedialog, messagebox, ttk

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
MODELS_DIR = DATA_DIR / "models"
OUTPUT_DIR = DATA_DIR / "output"
SETTINGS_PATH = DATA_DIR / "settings.json"
CATALOG_CACHE_PATH = DATA_DIR / "voices.json"
AVAILABILITY_CACHE_PATH = DATA_DIR / "availability.json"
VENDOR_DIR = ROOT_DIR / "vendor"

if VENDOR_DIR.exists():
    sys.path.insert(0, str(VENDOR_DIR))

CATALOG_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/voices.json?download=true"
)
HF_BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
REMOTE_TIMEOUT_SECONDS = 15
REMOTE_RETRY_COUNT = 3
APP_WINDOW_TITLE = "GetGoingFast.pro - Piper 1 Model Manager"
APP_DIALOG_TITLE = "GetGoingFast.pro"
APP_HEADER_TITLE = "GetGoingFast.pro : Piper 1 Model Manager"
APP_HEADER_LINK_TEXT = "youtube.com/@cognibuild"
APP_HEADER_LINK_URL = "https://www.youtube.com/@cognibuild"


def ensure_directories() -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def human_size(size_bytes: int) -> str:
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024.0 or unit == "GB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{size_bytes} B"


def read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def read_remote_bytes(url: str, *, method: str = "GET") -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, REMOTE_RETRY_COUNT + 1):
        try:
            request = urllib.request.Request(url, method=method)
            with urllib.request.urlopen(request, timeout=REMOTE_TIMEOUT_SECONDS) as response:
                return response.read()
        except (
            urllib.error.URLError,
            http.client.IncompleteRead,
            http.client.RemoteDisconnected,
            ConnectionResetError,
            socket.timeout,
            TimeoutError,
            OSError,
        ) as exc:
            last_error = exc
            if attempt < REMOTE_RETRY_COUNT:
                time.sleep(min(2 * attempt, 5))
            continue

    raise RuntimeError(f"Failed to read remote URL after retries: {url}") from last_error


def fetch_catalog(force_refresh: bool = False) -> dict[str, dict]:
    if CATALOG_CACHE_PATH.exists() and not force_refresh:
        try:
            return json.loads(CATALOG_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    try:
        catalog_bytes = read_remote_bytes(CATALOG_URL)
        catalog = json.loads(catalog_bytes.decode("utf-8"))
    except Exception:
        if CATALOG_CACHE_PATH.exists():
            return json.loads(CATALOG_CACHE_PATH.read_text(encoding="utf-8"))
        raise

    write_json(CATALOG_CACHE_PATH, catalog)
    return catalog


def remote_file_url(remote_path: str) -> str:
    quoted_path = urllib.parse.quote(remote_path, safe="/")
    return f"{HF_BASE_URL}{quoted_path}?download=true"


def load_availability_cache() -> dict:
    return read_json(
        AVAILABILITY_CACHE_PATH,
        {"voices": {}},
    )


def save_availability_cache(cache: dict) -> None:
    write_json(AVAILABILITY_CACHE_PATH, cache)


def update_voice_availability_cache(
    voice_key: str,
    voice: dict,
    *,
    available: bool,
    file_status: dict[str, bool] | None = None,
) -> None:
    cache = load_availability_cache()
    remote_paths = sorted(voice["files"].keys())
    cache.setdefault("voices", {})[voice_key] = {
        "available": available,
        "checked_at": int(time.time()),
        "remote_paths": remote_paths,
        "files": file_status or {},
    }
    save_availability_cache(cache)


def voice_remote_files_available(
    voice_key: str, voice: dict, cache: dict, force_refresh: bool
) -> bool:
    voice_cache = cache.setdefault("voices", {}).get(voice_key)
    remote_paths = sorted(voice["files"].keys())
    if (
        voice_cache
        and not force_refresh
        and voice_cache.get("remote_paths") == remote_paths
        and isinstance(voice_cache.get("available"), bool)
    ):
        return bool(voice_cache["available"])

    cache.setdefault("voices", {})[voice_key] = {
        "available": True,
        "checked_at": int(time.time()),
        "remote_paths": remote_paths,
        "files": {},
    }
    return True


def fetch_available_catalog(force_refresh: bool = False) -> tuple[dict[str, dict], int]:
    catalog = fetch_catalog(force_refresh=force_refresh)
    availability_cache = load_availability_cache()
    available_catalog: dict[str, dict] = {}
    hidden_count = 0

    for voice_key, voice in catalog.items():
        if voice_remote_files_available(
            voice_key, voice, availability_cache, False
        ):
            available_catalog[voice_key] = voice
        else:
            hidden_count += 1

    save_availability_cache(availability_cache)
    return available_catalog, hidden_count


def voice_file_map(voice: dict) -> list[tuple[str, Path, int]]:
    downloads: list[tuple[str, Path, int]] = []
    for remote_path, file_info in sorted(voice["files"].items()):
        local_path = MODELS_DIR / Path(remote_path).name
        downloads.append((remote_path, local_path, int(file_info.get("size_bytes", 0))))
    return downloads


def voice_is_installed(voice: dict) -> bool:
    downloads = voice_file_map(voice)
    if not downloads:
        return False

    return all(
        local_path.exists() and local_path.stat().st_size > 0
        for _, local_path, _ in downloads
    )


def voice_model_path(voice_key: str) -> Path:
    return MODELS_DIR / f"{voice_key}.onnx"


def voice_config_path(voice_key: str) -> Path:
    return MODELS_DIR / f"{voice_key}.onnx.json"


class PiperRuntime:
    def __init__(self) -> None:
        self._voice = None
        self._voice_key = None
        self._use_cuda = None
        self._lock = threading.Lock()

    def _load_voice(self, voice_key: str, *, use_cuda: bool):
        model_path = voice_model_path(voice_key)
        config_path = voice_config_path(voice_key)
        if not model_path.exists() or not config_path.exists():
            raise FileNotFoundError(
                f"Voice '{voice_key}' is not fully installed in {MODELS_DIR}."
            )

        try:
            from piper import PiperVoice
        except ImportError as exc:
            raise RuntimeError(
                "The Piper runtime is not installed yet. Run install.bat first."
            ) from exc

        if (
            self._voice is None
            or self._voice_key != voice_key
            or self._use_cuda != use_cuda
        ):
            self._voice = PiperVoice.load(
                model_path, config_path=config_path, use_cuda=use_cuda
            )
            self._voice_key = voice_key
            self._use_cuda = use_cuda

        return self._voice

    def _synthesis_config(
        self,
        *,
        speaker_id: int | None,
        length_scale: float | None,
        noise_scale: float | None,
        noise_w_scale: float | None,
        volume: float,
        normalize_audio: bool,
    ):
        try:
            from piper import SynthesisConfig
        except ImportError as exc:
            raise RuntimeError(
                "The Piper runtime is not installed yet. Run install.bat first."
            ) from exc

        return SynthesisConfig(
            speaker_id=speaker_id,
            length_scale=length_scale,
            noise_scale=noise_scale,
            noise_w_scale=noise_w_scale,
            volume=volume,
            normalize_audio=normalize_audio,
        )

    def synthesize_to_file(
        self,
        voice_key: str,
        text: str,
        destination: Path,
        *,
        use_cuda: bool,
        speaker_id: int | None,
        length_scale: float | None,
        noise_scale: float | None,
        noise_w_scale: float | None,
        volume: float,
        normalize_audio: bool,
    ) -> None:
        if not text.strip():
            raise ValueError("Enter some text to synthesize.")

        synth_config = self._synthesis_config(
            speaker_id=speaker_id,
            length_scale=length_scale,
            noise_scale=noise_scale,
            noise_w_scale=noise_w_scale,
            volume=volume,
            normalize_audio=normalize_audio,
        )

        with self._lock:
            voice = self._load_voice(voice_key, use_cuda=use_cuda)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(destination), "wb") as wav_file:
                voice.synthesize_wav(text, wav_file, syn_config=synth_config)

    def synthesize_to_bytes(
        self,
        voice_key: str,
        text: str,
        *,
        use_cuda: bool,
        speaker_id: int | None,
        length_scale: float | None,
        noise_scale: float | None,
        noise_w_scale: float | None,
        volume: float,
        normalize_audio: bool,
    ) -> bytes:
        if not text.strip():
            raise ValueError("Enter some text to synthesize.")

        synth_config = self._synthesis_config(
            speaker_id=speaker_id,
            length_scale=length_scale,
            noise_scale=noise_scale,
            noise_w_scale=noise_w_scale,
            volume=volume,
            normalize_audio=normalize_audio,
        )

        with self._lock:
            voice = self._load_voice(voice_key, use_cuda=use_cuda)
            with io.BytesIO() as wav_io:
                with wave.open(wav_io, "wb") as wav_file:
                    voice.synthesize_wav(text, wav_file, syn_config=synth_config)
                return wav_io.getvalue()


class LocalPiperApiServer:
    def __init__(self, app: "PiperManagerApp") -> None:
        self.app = app
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.host = "127.0.0.1"
        self.port = 5000

    def is_running(self) -> bool:
        return self.server is not None and self.thread is not None and self.thread.is_alive()

    def start(self, host: str, port: int) -> None:
        if self.is_running():
            raise RuntimeError("API server is already running.")

        outer = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "Piper1ManagerAPI/1.0"

            def do_GET(self) -> None:
                outer.handle_request(self)

            def do_POST(self) -> None:
                outer.handle_request(self)

            def log_message(self, _format: str, *_args) -> None:
                return

        try:
            self.server = ThreadingHTTPServer((host, port), Handler)
        except OSError as exc:
            raise RuntimeError(f"Could not start API server on {host}:{port}: {exc}") from exc

        self.host = host
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.server is None:
            return

        server = self.server
        thread = self.thread
        self.server = None
        self.thread = None
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=2)

    def handle_request(self, handler: BaseHTTPRequestHandler) -> None:
        try:
            if handler.command == "GET" and handler.path in ("/", "/health"):
                self.send_json(
                    handler,
                    200,
                    {
                        "status": "ok",
                        "host": self.host,
                        "port": self.port,
                        "active_voice": self.app.active_voice_name,
                    },
                )
                return

            if handler.command == "GET" and handler.path == "/voices":
                self.send_json(handler, 200, self.app.api_installed_voices_payload())
                return

            if handler.command == "GET" and handler.path == "/all-voices":
                self.send_json(handler, 200, self.app.catalog)
                return

            if handler.command == "POST" and handler.path in ("/", "/synthesize", "/tts"):
                body = self.read_json_body(handler)
                wav_bytes = self.app.api_synthesize(body)
                self.send_bytes(handler, 200, wav_bytes, "audio/wav")
                return

            self.send_json(handler, 404, {"error": "Not found"})
        except Exception as exc:
            self.send_json(handler, 400, {"error": str(exc)})

    def read_json_body(self, handler: BaseHTTPRequestHandler) -> dict:
        content_length = int(handler.headers.get("Content-Length", "0"))
        raw_body = handler.rfile.read(content_length) if content_length > 0 else b"{}"
        if not raw_body:
            return {}
        try:
            parsed = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Request body must be valid JSON.") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Request body must be a JSON object.")
        return parsed

    def send_json(
        self, handler: BaseHTTPRequestHandler, status_code: int, payload: dict
    ) -> None:
        response = json.dumps(payload, ensure_ascii=True, indent=2).encode("utf-8")
        handler.send_response(status_code)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(response)))
        handler.end_headers()
        handler.wfile.write(response)

    def send_bytes(
        self,
        handler: BaseHTTPRequestHandler,
        status_code: int,
        payload: bytes,
        content_type: str,
    ) -> None:
        handler.send_response(status_code)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)


class PiperManagerApp:
    def __init__(self, root: Tk) -> None:
        ensure_directories()
        self.root = root
        self.root.title(APP_WINDOW_TITLE)
        self.root.geometry("1420x980")
        self.root.minsize(1180, 820)

        self.runtime = PiperRuntime()
        self.api_server = LocalPiperApiServer(self)
        self.catalog: dict[str, dict] = {}
        self.catalog_keys: list[str] = []
        self.event_queue: queue.Queue = queue.Queue()
        self.busy_count = 0
        self.selected_voice_key: str | None = None
        self.hidden_voice_count = 0
        self.settings = read_json(
            SETTINGS_PATH,
            {
                "active_voice": "",
                "last_text": "Welcome to Piper.",
                "last_output_dir": str(OUTPUT_DIR),
                "use_cuda": False,
                "normalize_audio": True,
                "length_scale": "",
                "noise_scale": "",
                "noise_w_scale": "",
                "volume": "1.0",
                "speaker_id": "",
                "api_host": "127.0.0.1",
                "api_port": "5000",
            },
        )

        self.status_var = StringVar(value="Loading Piper voice catalog...")
        self.active_voice_var = StringVar(value=self.settings.get("active_voice", ""))
        self.search_var = StringVar()
        self.language_var = StringVar(value="All languages")
        self.installed_only_var = BooleanVar(value=False)
        self.use_cuda_var = BooleanVar(value=bool(self.settings.get("use_cuda", False)))
        self.normalize_audio_var = BooleanVar(
            value=bool(self.settings.get("normalize_audio", True))
        )
        self.length_scale_var = StringVar(
            value=str(self.settings.get("length_scale", ""))
        )
        self.noise_scale_var = StringVar(value=str(self.settings.get("noise_scale", "")))
        self.noise_w_scale_var = StringVar(
            value=str(self.settings.get("noise_w_scale", ""))
        )
        self.volume_var = StringVar(value=str(self.settings.get("volume", "1.0")))
        self.speaker_id_var = StringVar(value=str(self.settings.get("speaker_id", "")))
        self.api_host_var = StringVar(value=str(self.settings.get("api_host", "127.0.0.1")))
        self.api_port_var = StringVar(value=str(self.settings.get("api_port", "5000")))
        self.api_status_var = StringVar(value="API stopped")
        self.api_base_url_var = StringVar(value="")
        self.api_tts_url_var = StringVar(value="")
        self.api_voices_url_var = StringVar(value="")
        self.active_voice_name = str(self.settings.get("active_voice", "")).strip()
        self.use_cuda_enabled = bool(self.settings.get("use_cuda", False))
        self.normalize_audio_enabled = bool(self.settings.get("normalize_audio", True))

        self.detail_voice_var = StringVar(value="")
        self.detail_language_var = StringVar(value="")
        self.detail_quality_var = StringVar(value="")
        self.detail_speakers_var = StringVar(value="")
        self.detail_files_var = StringVar(value="")
        self.detail_status_var = StringVar(value="")
        self.detail_aliases_var = StringVar(value="")

        self.use_cuda_var.trace_add("write", self.on_use_cuda_toggled)
        self.normalize_audio_var.trace_add("write", self.on_normalize_audio_toggled)
        self.api_host_var.trace_add("write", self.on_api_address_changed)
        self.api_port_var.trace_add("write", self.on_api_address_changed)
        self._build_ui()
        self.refresh_api_endpoint_labels()
        self.text_input.insert("1.0", self.settings.get("last_text", ""))
        self.root.after(125, self._process_events)
        self.refresh_catalog(force_refresh=False)

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=3)
        self.root.columnconfigure(1, weight=2)
        self.root.rowconfigure(2, weight=1)

        brand_frame = ttk.Frame(self.root, padding=(12, 12, 12, 4))
        brand_frame.grid(row=0, column=0, columnspan=2, sticky="ew")
        brand_frame.columnconfigure(0, weight=1)
        ttk.Label(
            brand_frame,
            text=APP_HEADER_TITLE,
            font=("Segoe UI", 16, "bold"),
        ).grid(row=0, column=0, sticky="w")
        link_label = ttk.Label(
            brand_frame,
            text=APP_HEADER_LINK_TEXT,
            foreground="#0b57d0",
            cursor="hand2",
        )
        link_label.grid(row=0, column=1, sticky="e", padx=(12, 0))
        link_label.bind(
            "<Button-1>",
            lambda _event: webbrowser.open(APP_HEADER_LINK_URL),
        )

        toolbar = ttk.Frame(self.root, padding=12)
        toolbar.grid(row=1, column=0, columnspan=2, sticky="ew")
        toolbar.columnconfigure(6, weight=1)

        ttk.Button(
            toolbar, text="Refresh Catalog", command=lambda: self.refresh_catalog(True)
        ).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(
            toolbar, text="Open Models Folder", command=lambda: self.open_folder(MODELS_DIR)
        ).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(
            toolbar, text="Open Output Folder", command=lambda: self.open_folder(OUTPUT_DIR)
        ).grid(row=0, column=2, padx=(0, 8))
        ttk.Label(toolbar, text="Active Voice:").grid(row=0, column=3, padx=(10, 6))
        self.active_voice_combo = ttk.Combobox(
            toolbar,
            textvariable=self.active_voice_var,
            state="readonly",
            width=34,
        )
        self.active_voice_combo.grid(row=0, column=4, padx=(0, 8))
        self.active_voice_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self.on_active_voice_changed()
        )
        ttk.Label(toolbar, textvariable=self.status_var, anchor="w").grid(
            row=0, column=6, sticky="ew"
        )

        left_panel = ttk.Frame(self.root, padding=(12, 0, 6, 12))
        left_panel.grid(row=2, column=0, sticky="nsew")
        left_panel.columnconfigure(0, weight=1)
        left_panel.rowconfigure(1, weight=1)

        filter_frame = ttk.LabelFrame(left_panel, text="Voice Catalog", padding=12)
        filter_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        filter_frame.columnconfigure(1, weight=1)
        filter_frame.columnconfigure(3, weight=1)

        ttk.Label(filter_frame, text="Search").grid(row=0, column=0, sticky="w")
        search_entry = ttk.Entry(filter_frame, textvariable=self.search_var)
        search_entry.grid(row=0, column=1, sticky="ew", padx=(6, 12))
        search_entry.bind("<KeyRelease>", lambda _event: self.apply_filters())

        ttk.Label(filter_frame, text="Language").grid(row=0, column=2, sticky="w")
        self.language_combo = ttk.Combobox(
            filter_frame,
            textvariable=self.language_var,
            state="readonly",
            width=28,
            values=["All languages"],
        )
        self.language_combo.grid(row=0, column=3, sticky="ew", padx=(6, 12))
        self.language_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self.apply_filters()
        )

        ttk.Checkbutton(
            filter_frame,
            text="Installed only",
            variable=self.installed_only_var,
            command=self.apply_filters,
        ).grid(row=0, column=4, sticky="w")

        table_frame = ttk.Frame(left_panel)
        table_frame.grid(row=1, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        columns = ("installed", "voice", "language", "quality", "speakers", "size")
        self.voice_tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        self.voice_tree.grid(row=0, column=0, sticky="nsew")
        self.voice_tree.heading("installed", text="Status")
        self.voice_tree.heading("voice", text="Voice")
        self.voice_tree.heading("language", text="Language")
        self.voice_tree.heading("quality", text="Quality")
        self.voice_tree.heading("speakers", text="Speakers")
        self.voice_tree.heading("size", text="Model Size")
        self.voice_tree.column("installed", width=90, anchor="center")
        self.voice_tree.column("voice", width=250, anchor="w")
        self.voice_tree.column("language", width=240, anchor="w")
        self.voice_tree.column("quality", width=90, anchor="center")
        self.voice_tree.column("speakers", width=80, anchor="center")
        self.voice_tree.column("size", width=100, anchor="e")
        self.voice_tree.bind(
            "<<TreeviewSelect>>", lambda _event: self.on_voice_selected()
        )

        tree_scroll = ttk.Scrollbar(
            table_frame, orient="vertical", command=self.voice_tree.yview
        )
        tree_scroll.grid(row=0, column=1, sticky="ns")
        self.voice_tree.configure(yscrollcommand=tree_scroll.set)

        right_panel = ttk.Frame(self.root, padding=(6, 0, 12, 12))
        right_panel.grid(row=2, column=1, sticky="nsew")
        right_panel.columnconfigure(0, weight=1)
        right_panel.rowconfigure(3, weight=1)

        details = ttk.LabelFrame(right_panel, text="Selected Voice", padding=12)
        details.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        details.columnconfigure(1, weight=1)

        ttk.Label(details, text="Voice").grid(row=0, column=0, sticky="nw")
        ttk.Label(details, textvariable=self.detail_voice_var).grid(
            row=0, column=1, sticky="nw"
        )
        ttk.Label(details, text="Language").grid(row=1, column=0, sticky="nw")
        ttk.Label(details, textvariable=self.detail_language_var).grid(
            row=1, column=1, sticky="nw"
        )
        ttk.Label(details, text="Quality").grid(row=2, column=0, sticky="nw")
        ttk.Label(details, textvariable=self.detail_quality_var).grid(
            row=2, column=1, sticky="nw"
        )
        ttk.Label(details, text="Speakers").grid(row=3, column=0, sticky="nw")
        ttk.Label(details, textvariable=self.detail_speakers_var).grid(
            row=3, column=1, sticky="nw"
        )
        ttk.Label(details, text="Files").grid(row=4, column=0, sticky="nw")
        ttk.Label(details, textvariable=self.detail_files_var, wraplength=430).grid(
            row=4, column=1, sticky="nw"
        )
        ttk.Label(details, text="Aliases").grid(row=5, column=0, sticky="nw")
        ttk.Label(details, textvariable=self.detail_aliases_var, wraplength=430).grid(
            row=5, column=1, sticky="nw"
        )
        ttk.Label(details, text="Install Status").grid(row=6, column=0, sticky="nw")
        ttk.Label(details, textvariable=self.detail_status_var, wraplength=430).grid(
            row=6, column=1, sticky="nw"
        )

        action_row = ttk.Frame(details)
        action_row.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(action_row, text="Download", command=self.download_selected).grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Button(
            action_row,
            text="Redownload",
            command=lambda: self.download_selected(force=True),
        ).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(action_row, text="Delete", command=self.delete_selected).grid(
            row=0, column=2, padx=(0, 8)
        )
        ttk.Button(
            action_row, text="Use This Voice", command=self.set_selected_active_voice
        ).grid(row=0, column=3)

        synth = ttk.LabelFrame(right_panel, text="Synthesis", padding=12)
        synth.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        synth.columnconfigure(1, weight=1)
        synth.columnconfigure(3, weight=1)

        ttk.Label(synth, text="Speaker ID").grid(row=0, column=0, sticky="w")
        ttk.Entry(synth, textvariable=self.speaker_id_var, width=12).grid(
            row=0, column=1, sticky="ew", padx=(6, 12)
        )
        ttk.Label(synth, text="Volume").grid(row=0, column=2, sticky="w")
        ttk.Entry(synth, textvariable=self.volume_var, width=12).grid(
            row=0, column=3, sticky="ew", padx=(6, 0)
        )

        ttk.Label(synth, text="Length Scale").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(synth, textvariable=self.length_scale_var, width=12).grid(
            row=1, column=1, sticky="ew", padx=(6, 12), pady=(8, 0)
        )
        ttk.Label(synth, text="Noise Scale").grid(row=1, column=2, sticky="w", pady=(8, 0))
        ttk.Entry(synth, textvariable=self.noise_scale_var, width=12).grid(
            row=1, column=3, sticky="ew", padx=(6, 0), pady=(8, 0)
        )

        ttk.Label(synth, text="Noise W Scale").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(synth, textvariable=self.noise_w_scale_var, width=12).grid(
            row=2, column=1, sticky="ew", padx=(6, 12), pady=(8, 0)
        )
        ttk.Checkbutton(
            synth,
            text="Use CUDA",
            variable=self.use_cuda_var,
        ).grid(row=2, column=2, sticky="w", pady=(8, 0))
        ttk.Checkbutton(
            synth,
            text="Normalize Audio",
            variable=self.normalize_audio_var,
        ).grid(row=2, column=3, sticky="w", pady=(8, 0))

        api_frame = ttk.LabelFrame(right_panel, text="Local API", padding=12)
        api_frame.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        api_frame.columnconfigure(1, weight=1)
        api_frame.columnconfigure(3, weight=1)

        ttk.Label(api_frame, text="Host").grid(row=0, column=0, sticky="w")
        ttk.Entry(api_frame, textvariable=self.api_host_var, width=16).grid(
            row=0, column=1, sticky="ew", padx=(6, 12)
        )
        ttk.Label(api_frame, text="Port").grid(row=0, column=2, sticky="w")
        ttk.Entry(api_frame, textvariable=self.api_port_var, width=10).grid(
            row=0, column=3, sticky="ew", padx=(6, 0)
        )

        api_buttons = ttk.Frame(api_frame)
        api_buttons.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        ttk.Button(api_buttons, text="Start API", command=self.start_api_server).grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Button(api_buttons, text="Stop API", command=self.stop_api_server).grid(
            row=0, column=1, padx=(0, 8)
        )
        ttk.Button(api_buttons, text="Copy API URL", command=self.copy_api_url).grid(
            row=0, column=2
        )
        ttk.Label(api_frame, textvariable=self.api_status_var, wraplength=430).grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(10, 0)
        )
        ttk.Label(api_frame, text="Base API URL").grid(row=3, column=0, sticky="w", pady=(10, 0))
        ttk.Label(api_frame, textvariable=self.api_base_url_var, wraplength=430).grid(
            row=3, column=1, columnspan=3, sticky="w", pady=(10, 0)
        )
        ttk.Label(api_frame, text="TTS POST").grid(row=4, column=0, sticky="w", pady=(6, 0))
        ttk.Label(api_frame, textvariable=self.api_tts_url_var, wraplength=430).grid(
            row=4, column=1, columnspan=3, sticky="w", pady=(6, 0)
        )
        ttk.Label(api_frame, text="Voices GET").grid(row=5, column=0, sticky="w", pady=(6, 0))
        ttk.Label(api_frame, textvariable=self.api_voices_url_var, wraplength=430).grid(
            row=5, column=1, columnspan=3, sticky="w", pady=(6, 0)
        )

        text_frame = ttk.LabelFrame(right_panel, text="Text Input", padding=12)
        text_frame.grid(row=3, column=0, sticky="nsew")
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)

        self.text_input = Text(text_frame, wrap="word", height=12)
        self.text_input.grid(row=0, column=0, sticky="nsew")
        text_scroll = ttk.Scrollbar(
            text_frame, orient="vertical", command=self.text_input.yview
        )
        text_scroll.grid(row=0, column=1, sticky="ns")
        self.text_input.configure(yscrollcommand=text_scroll.set)

        synth_actions = ttk.Frame(text_frame)
        synth_actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Button(synth_actions, text="Preview Audio", command=self.preview_audio).grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Button(synth_actions, text="Save WAV As...", command=self.save_audio_as).grid(
            row=0, column=1, padx=(0, 8)
        )
        ttk.Button(synth_actions, text="Stop Playback", command=self.stop_playback).grid(
            row=0, column=2
        )

    def set_status(self, message: str) -> None:
        self.status_var.set(message)

    def queue_status(self, message: str) -> None:
        self.event_queue.put(("status", message))

    def increment_busy(self) -> None:
        self.busy_count += 1

    def decrement_busy(self) -> None:
        self.busy_count = max(0, self.busy_count - 1)

    def run_background(self, label: str, target, callback=None) -> None:
        self.increment_busy()
        self.set_status(label)

        def worker() -> None:
            try:
                result = target()
            except Exception as exc:
                self.event_queue.put(
                    (
                        "error",
                        label,
                        f"{exc}",
                        traceback.format_exc(),
                    )
                )
            else:
                self.event_queue.put(("result", label, callback, result))
            finally:
                self.event_queue.put(("done",))

        threading.Thread(target=worker, daemon=True).start()

    def _process_events(self) -> None:
        while True:
            try:
                event = self.event_queue.get_nowait()
            except queue.Empty:
                break

            event_type = event[0]
            if event_type == "error":
                _, label, message, trace = event
                self.set_status(f"{label} failed")
                messagebox.showerror(APP_DIALOG_TITLE, f"{message}\n\n{trace}")
            elif event_type == "result":
                _, label, callback, result = event
                if callback is not None:
                    callback(result)
                else:
                    self.set_status(f"{label} complete")
            elif event_type == "status":
                _, message = event
                self.set_status(message)
            elif event_type == "done":
                self.decrement_busy()

        self.root.after(125, self._process_events)

    def refresh_catalog(self, force_refresh: bool) -> None:
        def task() -> tuple[dict[str, dict], int]:
            return fetch_available_catalog(force_refresh=force_refresh)

        self.run_background("Refreshing voice catalog...", task, self.on_catalog_loaded)

    def on_catalog_loaded(self, result: tuple[dict[str, dict], int]) -> None:
        catalog, hidden_count = result
        self.catalog = catalog
        self.catalog_keys = sorted(catalog.keys())
        self.hidden_voice_count = hidden_count
        language_names = sorted({self.language_label(voice) for voice in self.catalog.values()})
        self.language_combo["values"] = ["All languages", *language_names]
        self.active_voice_combo["values"] = self.installed_voice_keys()
        self.apply_filters()
        active_voice = self.active_voice_var.get()
        if active_voice and active_voice in self.catalog:
            self.select_voice(active_voice)
        if hidden_count > 0:
            self.set_status(
                f"Loaded {len(self.catalog)} working voices. Hid {hidden_count} broken catalog entries."
            )
        else:
            self.set_status(f"Loaded {len(self.catalog)} working voices from Piper catalog.")

    def installed_voice_keys(self) -> list[str]:
        return [key for key in self.catalog_keys if voice_is_installed(self.catalog[key])]

    def language_label(self, voice: dict) -> str:
        language = voice["language"]
        return (
            f"{language['name_english']} ({language['code']})"
            f" - {language['country_english']}"
        )

    def apply_filters(self) -> None:
        search_text = self.search_var.get().strip().lower()
        selected_language = self.language_var.get()
        installed_only = self.installed_only_var.get()

        existing_selection = self.selected_voice_key

        for item in self.voice_tree.get_children():
            self.voice_tree.delete(item)

        for key in self.catalog_keys:
            voice = self.catalog[key]
            installed = voice_is_installed(voice)
            if installed_only and not installed:
                continue

            language_label = self.language_label(voice)
            search_blob = " ".join(
                [
                    key,
                    voice["name"],
                    voice["quality"],
                    voice["language"]["name_english"],
                    voice["language"]["code"],
                    voice["language"]["country_english"],
                    " ".join(voice.get("aliases", [])),
                ]
            ).lower()

            if search_text and search_text not in search_blob:
                continue

            if selected_language != "All languages" and language_label != selected_language:
                continue

            total_size = sum(size for _, _, size in voice_file_map(voice))
            self.voice_tree.insert(
                "",
                "end",
                iid=key,
                values=(
                    "Installed" if installed else "Available",
                    key,
                    language_label,
                    voice["quality"],
                    voice["num_speakers"],
                    human_size(total_size),
                ),
            )

        self.active_voice_combo["values"] = self.installed_voice_keys()

        if existing_selection and self.voice_tree.exists(existing_selection):
            self.voice_tree.selection_set(existing_selection)
            self.voice_tree.focus(existing_selection)
            self.on_voice_selected()
        elif self.voice_tree.get_children():
            first = self.voice_tree.get_children()[0]
            self.voice_tree.selection_set(first)
            self.voice_tree.focus(first)
            self.on_voice_selected()
        else:
            self.selected_voice_key = None
            self.clear_details()

    def clear_details(self) -> None:
        self.detail_voice_var.set("")
        self.detail_language_var.set("")
        self.detail_quality_var.set("")
        self.detail_speakers_var.set("")
        self.detail_files_var.set("")
        self.detail_status_var.set("")
        self.detail_aliases_var.set("")

    def select_voice(self, voice_key: str) -> None:
        if self.voice_tree.exists(voice_key):
            self.voice_tree.selection_set(voice_key)
            self.voice_tree.focus(voice_key)
            self.voice_tree.see(voice_key)
            self.on_voice_selected()

    def on_voice_selected(self) -> None:
        selection = self.voice_tree.selection()
        if not selection:
            self.selected_voice_key = None
            self.clear_details()
            return

        voice_key = selection[0]
        voice = self.catalog[voice_key]
        self.selected_voice_key = voice_key

        self.detail_voice_var.set(voice_key)
        self.detail_language_var.set(self.language_label(voice))
        self.detail_quality_var.set(voice["quality"])
        self.detail_speakers_var.set(str(voice["num_speakers"]))
        self.detail_aliases_var.set(", ".join(voice.get("aliases", [])) or "None")
        self.detail_status_var.set(
            f"Installed in {MODELS_DIR}" if voice_is_installed(voice) else "Not downloaded yet"
        )
        self.detail_files_var.set(
            ", ".join(Path(remote_path).name for remote_path, _, _ in voice_file_map(voice))
        )

    def set_selected_active_voice(self) -> None:
        if not self.selected_voice_key:
            return

        voice = self.catalog[self.selected_voice_key]
        if not voice_is_installed(voice):
            if not messagebox.askyesno(
                APP_DIALOG_TITLE,
                "That voice is not installed yet. Download it now?",
            ):
                return
            self.download_selected(force=False, set_active_after=True)
            return

        self.active_voice_var.set(self.selected_voice_key)
        self.on_active_voice_changed()
        self.set_status(f"Active voice set to {self.selected_voice_key}.")

    def on_active_voice_changed(self) -> None:
        self.active_voice_name = self.active_voice_var.get().strip()
        self.settings["active_voice"] = self.active_voice_name
        self.save_settings()
        if self.api_server.is_running():
            self.update_api_status()

    def on_use_cuda_toggled(self, *_args) -> None:
        self.use_cuda_enabled = bool(self.use_cuda_var.get())

    def on_normalize_audio_toggled(self, *_args) -> None:
        self.normalize_audio_enabled = bool(self.normalize_audio_var.get())

    def on_api_address_changed(self, *_args) -> None:
        self.refresh_api_endpoint_labels()

    def api_base_url(self) -> str:
        host = self.api_host_var.get().strip() or "127.0.0.1"
        port = self.api_port_var.get().strip() or "5000"
        return f"http://{host}:{port}"

    def refresh_api_endpoint_labels(self) -> None:
        base_url = self.api_base_url()
        self.api_base_url_var.set(base_url)
        self.api_tts_url_var.set(
            f"{base_url}  or  {base_url}/tts  or  {base_url}/synthesize"
        )
        self.api_voices_url_var.set(f"{base_url}/voices")

    def download_selected(self, force: bool = False, set_active_after: bool = False) -> None:
        if not self.selected_voice_key:
            return

        voice_key = self.selected_voice_key
        voice = self.catalog[voice_key]

        def task() -> str:
            for remote_path, local_path, _size in voice_file_map(voice):
                if local_path.exists() and local_path.stat().st_size > 0 and not force:
                    continue

                local_path.parent.mkdir(parents=True, exist_ok=True)
                url = remote_file_url(remote_path)
                try:
                    with urllib.request.urlopen(
                        url, timeout=REMOTE_TIMEOUT_SECONDS
                    ) as response, open(local_path, "wb") as output_file:
                        while True:
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            output_file.write(chunk)
                except urllib.error.HTTPError as exc:
                    update_voice_availability_cache(
                        voice_key,
                        voice,
                        available=False,
                        file_status={remote_path: False},
                    )
                    raise RuntimeError(
                        f"The upstream Piper catalog entry for {voice_key} is broken ({exc.code} for {Path(remote_path).name}). "
                        "The app has hidden it from the catalog for future refreshes."
                    ) from exc

            update_voice_availability_cache(voice_key, voice, available=True)
            return voice_key

        def on_complete(downloaded_voice_key: str) -> None:
            self.apply_filters()
            self.select_voice(downloaded_voice_key)
            self.active_voice_combo["values"] = self.installed_voice_keys()
            if set_active_after:
                self.active_voice_var.set(downloaded_voice_key)
                self.on_active_voice_changed()
            self.set_status(f"Downloaded {downloaded_voice_key}.")

        action = "Redownloading" if force else "Downloading"
        self.run_background(f"{action} {voice_key}...", task, on_complete)

    def delete_selected(self) -> None:
        if not self.selected_voice_key:
            return

        voice_key = self.selected_voice_key
        voice = self.catalog[voice_key]
        if not voice_is_installed(voice):
            messagebox.showinfo(APP_DIALOG_TITLE, "That voice is not installed.")
            return

        if not messagebox.askyesno(
            APP_DIALOG_TITLE,
            f"Delete local files for {voice_key}?",
        ):
            return

        for _remote_path, local_path, _size in voice_file_map(voice):
            if local_path.exists():
                local_path.unlink()

        if self.active_voice_var.get() == voice_key:
            self.active_voice_var.set("")
            self.on_active_voice_changed()

        self.apply_filters()
        self.set_status(f"Deleted {voice_key}.")

    def parse_optional_float(self, raw_value: str, label: str) -> float | None:
        value = raw_value.strip()
        if not value:
            return None
        try:
            return float(value)
        except ValueError as exc:
            raise ValueError(f"{label} must be a number.") from exc

    def parse_optional_int(self, raw_value: str, label: str) -> int | None:
        value = raw_value.strip()
        if not value:
            return None
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"{label} must be an integer.") from exc

    def synthesis_options(self) -> dict:
        volume = self.parse_optional_float(self.volume_var.get(), "Volume")
        if volume is None:
            volume = 1.0

        return {
            "speaker_id": self.parse_optional_int(self.speaker_id_var.get(), "Speaker ID"),
            "length_scale": self.parse_optional_float(
                self.length_scale_var.get(), "Length Scale"
            ),
            "noise_scale": self.parse_optional_float(
                self.noise_scale_var.get(), "Noise Scale"
            ),
            "noise_w_scale": self.parse_optional_float(
                self.noise_w_scale_var.get(), "Noise W Scale"
            ),
            "volume": volume,
            "normalize_audio": self.normalize_audio_var.get(),
            "use_cuda": self.use_cuda_var.get(),
        }

    def current_text(self) -> str:
        return self.text_input.get("1.0", "end").strip()

    def preview_audio(self) -> None:
        voice_key = self.active_voice_var.get().strip()
        if not voice_key:
            messagebox.showinfo(APP_DIALOG_TITLE, "Choose an active installed voice first.")
            return

        text = self.current_text()
        preview_path = OUTPUT_DIR / f"preview_{int(time.time())}.wav"
        self.run_synthesis(voice_key, text, preview_path, preview=True)

    def save_audio_as(self) -> None:
        voice_key = self.active_voice_var.get().strip()
        if not voice_key:
            messagebox.showinfo(APP_DIALOG_TITLE, "Choose an active installed voice first.")
            return

        suggested_name = f"{voice_key}_{int(time.time())}.wav"
        destination = filedialog.asksaveasfilename(
            title="Save WAV As",
            defaultextension=".wav",
            initialdir=str(OUTPUT_DIR),
            initialfile=suggested_name,
            filetypes=[("WAV audio", "*.wav")],
        )
        if not destination:
            return

        self.run_synthesis(voice_key, self.current_text(), Path(destination), preview=False)

    def run_synthesis(
        self, voice_key: str, text: str, destination: Path, *, preview: bool
    ) -> None:
        options = self.synthesis_options()

        def task() -> Path:
            self.runtime.synthesize_to_file(
                voice_key,
                text,
                destination,
                use_cuda=options["use_cuda"],
                speaker_id=options["speaker_id"],
                length_scale=options["length_scale"],
                noise_scale=options["noise_scale"],
                noise_w_scale=options["noise_w_scale"],
                volume=options["volume"],
                normalize_audio=options["normalize_audio"],
            )
            return destination

        def on_complete(result_path: Path) -> None:
            self.settings.update(
                {
                    "active_voice": voice_key,
                    "last_text": text,
                    "last_output_dir": str(result_path.parent),
                    "use_cuda": options["use_cuda"],
                    "normalize_audio": options["normalize_audio"],
                    "length_scale": self.length_scale_var.get(),
                    "noise_scale": self.noise_scale_var.get(),
                    "noise_w_scale": self.noise_w_scale_var.get(),
                    "volume": self.volume_var.get(),
                    "speaker_id": self.speaker_id_var.get(),
                }
            )
            self.save_settings()
            if preview:
                winsound.PlaySound(
                    str(result_path), winsound.SND_FILENAME | winsound.SND_ASYNC
                )
                self.set_status(f"Preview ready: {result_path.name}")
            else:
                self.set_status(f"Saved WAV to {result_path}")
                messagebox.showinfo(APP_DIALOG_TITLE, f"Saved WAV to:\n{result_path}")

        self.run_background(f"Synthesizing with {voice_key}...", task, on_complete)

    def api_installed_voices_payload(self) -> dict:
        voices_payload: dict[str, dict] = {}
        for voice_key in self.installed_voice_keys():
            config_path = voice_config_path(voice_key)
            if not config_path.exists():
                continue
            try:
                voices_payload[voice_key] = json.loads(
                    config_path.read_text(encoding="utf-8")
                )
            except Exception:
                voices_payload[voice_key] = {"error": "Could not read voice config."}
        return voices_payload

    def api_optional_float(self, value, field_name: str) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must be a number.") from exc

    def api_optional_bool(self, value, default: bool, field_name: str) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in ("1", "true", "yes", "on"):
                return True
            if normalized in ("0", "false", "no", "off"):
                return False
        raise ValueError(f"{field_name} must be a boolean.")

    def api_synthesize(self, payload: dict) -> bytes:
        text = str(payload.get("text", "")).strip()
        if not text:
            raise ValueError("The 'text' field is required.")

        voice_key = str(payload.get("voice", "")).strip() or self.active_voice_name
        if not voice_key:
            raise ValueError("No voice was provided and no active voice is selected.")
        if voice_key not in self.catalog:
            raise ValueError(f"Unknown voice: {voice_key}")
        if not voice_is_installed(self.catalog[voice_key]):
            raise ValueError(f"Voice is not installed: {voice_key}")

        speaker_id = payload.get("speaker_id")
        if speaker_id is not None:
            try:
                speaker_id = int(speaker_id)
            except (TypeError, ValueError) as exc:
                raise ValueError("speaker_id must be an integer.") from exc

        volume = self.api_optional_float(payload.get("volume"), "volume")
        if volume is None:
            volume = 1.0

        wav_bytes = self.runtime.synthesize_to_bytes(
            voice_key,
            text,
            use_cuda=self.api_optional_bool(
                payload.get("use_cuda"), self.use_cuda_enabled, "use_cuda"
            ),
            speaker_id=speaker_id,
            length_scale=self.api_optional_float(
                payload.get("length_scale"), "length_scale"
            ),
            noise_scale=self.api_optional_float(
                payload.get("noise_scale"), "noise_scale"
            ),
            noise_w_scale=self.api_optional_float(
                payload.get("noise_w_scale"), "noise_w_scale"
            ),
            volume=volume,
            normalize_audio=self.api_optional_bool(
                payload.get("normalize_audio"),
                self.normalize_audio_enabled,
                "normalize_audio",
            ),
        )
        self.queue_status(f"Served API synthesis for {voice_key}.")
        return wav_bytes

    def start_api_server(self) -> None:
        host = self.api_host_var.get().strip() or "127.0.0.1"
        try:
            port = int(self.api_port_var.get().strip() or "5000")
            if port < 1 or port > 65535:
                raise ValueError("API port must be between 1 and 65535.")

            self.api_server.start(host, port)
        except Exception as exc:
            messagebox.showerror(APP_DIALOG_TITLE, str(exc))
            return

        self.api_port_var.set(str(self.api_server.port))
        self.settings["api_host"] = host
        self.settings["api_port"] = str(self.api_server.port)
        self.save_settings()
        self.refresh_api_endpoint_labels()
        self.update_api_status()
        self.set_status(f"API server started on http://{host}:{self.api_server.port}")

    def stop_api_server(self) -> None:
        self.api_server.stop()
        self.refresh_api_endpoint_labels()
        self.update_api_status()
        self.set_status("API server stopped.")

    def update_api_status(self) -> None:
        if self.api_server.is_running():
            active_voice = self.active_voice_name or "(none selected)"
            self.api_status_var.set(
                f"Running at http://{self.api_server.host}:{self.api_server.port} using default voice {active_voice}"
            )
        else:
            self.api_status_var.set("API stopped")

    def copy_api_url(self) -> None:
        if not self.api_server.is_running():
            messagebox.showinfo(APP_DIALOG_TITLE, "Start the API first.")
            return

        api_url = f"http://{self.api_server.host}:{self.api_server.port}"
        self.root.clipboard_clear()
        self.root.clipboard_append(api_url)
        self.set_status(f"Copied API URL: {api_url}")

    def stop_playback(self) -> None:
        winsound.PlaySound(None, 0)
        self.set_status("Playback stopped.")

    def save_settings(self) -> None:
        write_json(SETTINGS_PATH, self.settings)

    def open_folder(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)


def main() -> None:
    ensure_directories()
    root = Tk()
    app = PiperManagerApp(root)
    root.protocol("WM_DELETE_WINDOW", lambda: on_close(app, root))
    root.mainloop()


def on_close(app: PiperManagerApp, root: Tk) -> None:
    app.settings.update(
        {
            "active_voice": app.active_voice_var.get(),
            "last_text": app.current_text(),
            "use_cuda": app.use_cuda_var.get(),
            "normalize_audio": app.normalize_audio_var.get(),
            "length_scale": app.length_scale_var.get(),
            "noise_scale": app.noise_scale_var.get(),
            "noise_w_scale": app.noise_w_scale_var.get(),
            "volume": app.volume_var.get(),
            "speaker_id": app.speaker_id_var.get(),
            "api_host": app.api_host_var.get(),
            "api_port": app.api_port_var.get(),
        }
    )
    app.save_settings()
    app.api_server.stop()
    winsound.PlaySound(None, 0)
    root.destroy()


if __name__ == "__main__":
    main()

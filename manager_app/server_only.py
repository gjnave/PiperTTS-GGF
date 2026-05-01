from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import main as manager_main


class HeadlessPiperServerApp:
    def __init__(
        self,
        *,
        active_voice: str,
        use_cuda: bool,
        normalize_audio: bool,
        catalog: dict[str, dict],
        catalog_keys: list[str],
    ) -> None:
        self.runtime = manager_main.PiperRuntime()
        self.api_server = manager_main.LocalPiperApiServer(self)
        self.catalog = catalog
        self.catalog_keys = catalog_keys
        self.active_voice_name = active_voice
        self.use_cuda_enabled = use_cuda
        self.normalize_audio_enabled = normalize_audio

    def installed_voice_keys(self) -> list[str]:
        return [
            key for key in self.catalog_keys if manager_main.voice_is_installed(self.catalog[key])
        ]

    def queue_status(self, message: str) -> None:
        print(message, flush=True)

    def api_installed_voices_payload(self) -> dict:
        voices_payload: dict[str, dict] = {}
        for voice_key in self.installed_voice_keys():
            config_path = manager_main.voice_config_path(voice_key)
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
        if not manager_main.voice_is_installed(self.catalog[voice_key]):
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

        return self.runtime.synthesize_to_bytes(
            voice_key,
            text,
            use_cuda=self.api_optional_bool(
                payload.get("use_cuda"), self.use_cuda_enabled, "use_cuda"
            ),
            speaker_id=speaker_id,
            length_scale=self.api_optional_float(
                payload.get("length_scale"), "length_scale"
            ),
            noise_scale=self.api_optional_float(payload.get("noise_scale"), "noise_scale"),
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Piper1 Manager as a local API server without the GUI."
    )
    parser.add_argument("--host", default=None, help="Server host, default from settings")
    parser.add_argument("--port", type=int, default=None, help="Server port, default from settings")
    parser.add_argument("--voice", default=None, help="Default voice id, default from settings")
    parser.add_argument(
        "--use-cuda",
        action="store_true",
        help="Prefer CUDA when available for synthesis requests",
    )
    parser.add_argument(
        "--no-normalize",
        action="store_true",
        help="Disable audio normalization by default",
    )
    return parser.parse_args()


def resolve_default_voice(catalog: dict[str, dict], preferred_voice: str) -> str:
    if preferred_voice and preferred_voice in catalog and manager_main.voice_is_installed(
        catalog[preferred_voice]
    ):
        return preferred_voice

    for voice_key in sorted(catalog.keys()):
        if manager_main.voice_is_installed(catalog[voice_key]):
            return voice_key

    raise RuntimeError(
        f"No installed voices were found in {manager_main.MODELS_DIR}. Download a voice first."
    )


def main() -> None:
    args = parse_args()
    manager_main.ensure_directories()
    settings = manager_main.read_json(manager_main.SETTINGS_PATH, {})
    catalog, hidden_count = manager_main.fetch_available_catalog(force_refresh=False)
    catalog_keys = sorted(catalog.keys())

    host = args.host or str(settings.get("api_host", "127.0.0.1"))
    port = args.port or int(str(settings.get("api_port", "5000")))
    preferred_voice = args.voice or str(settings.get("active_voice", "")).strip()
    active_voice = resolve_default_voice(catalog, preferred_voice)
    use_cuda = bool(args.use_cuda or settings.get("use_cuda", False))
    normalize_audio = not args.no_normalize and bool(
        settings.get("normalize_audio", True)
    )

    app = HeadlessPiperServerApp(
        active_voice=active_voice,
        use_cuda=use_cuda,
        normalize_audio=normalize_audio,
        catalog=catalog,
        catalog_keys=catalog_keys,
    )
    app.api_server.start(host, port)

    print("Piper1 Manager API server is running.", flush=True)
    print(f"Base API URL: http://{app.api_server.host}:{app.api_server.port}", flush=True)
    print(
        f"TTS POST: http://{app.api_server.host}:{app.api_server.port}  or  "
        f"http://{app.api_server.host}:{app.api_server.port}/tts  or  "
        f"http://{app.api_server.host}:{app.api_server.port}/synthesize",
        flush=True,
    )
    print(
        f"Voices GET: http://{app.api_server.host}:{app.api_server.port}/voices",
        flush=True,
    )
    print(f"Default voice: {app.active_voice_name}", flush=True)
    if hidden_count > 0:
        print(f"Hidden broken catalog entries: {hidden_count}", flush=True)
    print("Press Ctrl+C to stop the server.", flush=True)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping Piper1 Manager API server...", flush=True)
    finally:
        app.api_server.stop()


if __name__ == "__main__":
    main()

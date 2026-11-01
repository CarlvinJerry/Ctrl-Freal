import subprocess
import asyncio
import json
import os
import secrets
import threading
import time
import re
import hashlib
from tv_pairing import discover_android, discover_webos, start_pairing, pairing_state, finish_pairing
from pathlib import Path
from concurrent.futures import TimeoutError as FutureTimeout

from flask import (
    Flask, jsonify, redirect, render_template,
    request, session
)
from waitress import serve
from androidtvremote2 import AndroidTVRemote
from version import APP_VERSION
from jack_sparrow import JackSparrow, INDEXERS
import sqlite3

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "tvs.json"
PREFERENCES = BASE / "preferences.json"
SCREENSAVER_BACKUP = BASE / ".screensaver_previous.json"
GUEST_ACCESS = BASE / ".guest_access.json"
PIN = (BASE / ".remote_pin").read_text().strip()
SECRET = (BASE / ".session_secret").read_text().strip()
PORT = int(os.getenv("REMOTE_PORT", "8091"))
MAGICMIRROR_URL = os.getenv("REMOTE_MAGICMIRROR_URL", "http://192.168.1.21:8080/")
VIEWER_PACKAGE = "com.ctrlfreak.magicmirror"
VIEWER_VERSION_CODE = 8
VIEWER_DREAM = f"{VIEWER_PACKAGE}/.MirrorDreamService"
VIEWER_APK = BASE / "static" / "magicmirror-viewer.apk"
JACK = JackSparrow(BASE, media_root=os.getenv("JACK_MEDIA_ROOT", "/media/carlvinjerry/Views"))

DEFAULT_TVS = {
    "tcl": {
        "name": "Living Room TCL",
        "host": "192.168.1.5",
        "cert": "client_cert.pem",
        "key": "client_key.pem"
    }
}

if not CONFIG.exists():
    CONFIG.write_text(json.dumps(DEFAULT_TVS, indent=2))

app = Flask(__name__)
# Disable static asset caching during UI development
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

@app.after_request
def disable_static_cache(response):
    if request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response
app.secret_key = SECRET
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    PERMANENT_SESSION_LIFETIME=86400 * 30,
    MAX_CONTENT_LENGTH=16384,
)

KEYS = {
    "up": "DPAD_UP", "down": "DPAD_DOWN",
    "left": "DPAD_LEFT", "right": "DPAD_RIGHT",
    "ok": "DPAD_CENTER", "home": "HOME",
    "back": "BACK", "menu": "MENU",
    "volup": "VOLUME_UP", "voldown": "VOLUME_DOWN",
    "mute": "VOLUME_MUTE", "power": "POWER",
    "play": "MEDIA_PLAY_PAUSE",
    "previous": "MEDIA_PREVIOUS",
    "next": "MEDIA_NEXT",
    "rewind": "MEDIA_REWIND",
    "forward": "MEDIA_FAST_FORWARD",
    "enter": "ENTER", "delete": "DEL",
    "settings": "SETTINGS",
    "input": "TV_INPUT",
}

APPS = {
    "plex": ("Plex", "com.plexapp.android"),
    "youtube": ("YouTube", "com.google.android.youtube.tv"),
    "spotify": ("Spotify", "com.spotify.tv.android"),
    "netflix": ("Netflix", "com.netflix.ninja"),
}
APP_CACHE = {}
APP_CACHE_LOCK = threading.Lock()
CONFIG_LOCK = threading.Lock()
PREFERENCES_LOCK = threading.Lock()
SCREENSAVER_LOCK = threading.Lock()
GUEST_LOCK = threading.Lock()
RECOVERY_LOCK = asyncio.Lock()
PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+$")
SCENE_ID_RE = re.compile(r"^[a-z0-9_-]{1,40}$")
APP_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
THEMES = {"dark-glass", "light-glass", "midnight", "dark", "light"}
ACCENTS = {"blue", "violet", "mint", "coral", "amber", "pink"}


def read_preferences():
    if not PREFERENCES.exists():
        return None
    return json.loads(PREFERENCES.read_text())


def validate_preferences(data):
    if not isinstance(data, dict):
        raise ValueError("Invalid preferences")
    theme, accent, haptics = data.get("theme"), data.get("accent"), data.get("haptics")
    if theme not in THEMES or accent not in ACCENTS or not isinstance(haptics, bool):
        raise ValueError("Invalid appearance preferences")
    favorites = data.get("favorites")
    if not isinstance(favorites, dict) or len(favorites) > 30:
        raise ValueError("Invalid favorites")
    clean_favorites = {}
    for tv_id, ids in favorites.items():
        if not isinstance(tv_id, str) or not SCENE_ID_RE.fullmatch(tv_id):
            raise ValueError("Invalid favorite TV")
        if not isinstance(ids, list) or len(ids) > 30 or any(
            not isinstance(item, str) or not APP_ID_RE.fullmatch(item) for item in ids
        ):
            raise ValueError("Invalid favorite apps")
        clean_favorites[tv_id] = list(dict.fromkeys(ids))
    scenes = data.get("scenes")
    if not isinstance(scenes, list) or len(scenes) > 12:
        raise ValueError("Too many scenes")
    clean_scenes = []
    tvs = read_tvs()
    for item in scenes:
        if not isinstance(item, dict):
            raise ValueError("Invalid scene")
        scene_id, name, tv_id, app_id, volume = (
            item.get("id"), item.get("name"), item.get("tv"), item.get("app"), item.get("volume")
        )
        if not isinstance(scene_id, str) or not SCENE_ID_RE.fullmatch(scene_id):
            raise ValueError("Invalid scene ID")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
            raise ValueError("Enter a scene name")
        if tv_id not in tvs:
            raise ValueError("Unknown scene TV")
        if app_id is not None and (not isinstance(app_id, str) or not APP_ID_RE.fullmatch(app_id)):
            raise ValueError("Invalid scene app")
        if volume is not None and (not isinstance(volume, int) or isinstance(volume, bool) or not 0 <= volume <= 100):
            raise ValueError("Scene volume must be 0–100")
        if app_id is None and volume is None:
            raise ValueError("Choose an app or volume for the scene")
        clean_scenes.append({"id": scene_id, "name": name.strip(), "tv": tv_id,
                             "app": app_id, "volume": volume})
    if len({item["id"] for item in clean_scenes}) != len(clean_scenes):
        raise ValueError("Duplicate scene IDs")
    return {"theme": theme, "accent": accent, "haptics": haptics,
            "favorites": clean_favorites, "scenes": clean_scenes}


def save_preferences(data, only_if_missing=False):
    clean = validate_preferences(data)
    with PREFERENCES_LOCK:
        if only_if_missing and PREFERENCES.exists():
            return read_preferences()
        temp = PREFERENCES.with_name(PREFERENCES.name + ".new")
        temp.write_text(json.dumps(clean, indent=2))
        os.chmod(temp, 0o600)
        os.replace(temp, PREFERENCES)
    return clean


def adb_command(host, *args, timeout=5):
    serial = f"{host}:5555"
    command = ["adb", "-s", serial, *args]
    try:
        return subprocess.run(command, capture_output=True, text=True,
                              timeout=timeout, check=True).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        subprocess.run(["adb", "connect", serial], capture_output=True,
                       text=True, timeout=timeout, check=True)
        return subprocess.run(command, capture_output=True, text=True,
                              timeout=timeout, check=True).stdout.strip()


def adb_live_status(host):
    """Read values that some Android TV Remote implementations never publish."""
    result = {}
    try:
        window = adb_command(host, "shell", "dumpsys", "window", timeout=3)
        match = re.search(r"mCurrentFocus=.*?\s([A-Za-z][\w.]+)/", window)
        if match:
            result["current_app"] = match.group(1)
    except (subprocess.SubprocessError, OSError):
        pass
    try:
        audio = adb_command(host, "shell", "dumpsys", "audio", timeout=3)
        music = audio.split("- STREAM_MUSIC:", 1)[1].split("- STREAM_", 1)[0]
        maximum = re.search(r"^\s*Max:\s*(\d+)", music, re.M)
        level = re.search(r"^\s*streamVolume:\s*(\d+)", music, re.M)
        muted = re.search(r"^\s*Muted:\s*(true|false)", music, re.M)
        if level:
            result["volume"] = {"level": int(level.group(1)),
                                "maximum": int(maximum.group(1)) if maximum else None,
                                "muted": muted.group(1) == "true" if muted else None}
    except (subprocess.SubprocessError, OSError, IndexError):
        pass
    return result


def ensure_magicmirror_viewer(host):
    if not VIEWER_APK.is_file():
        raise FileNotFoundError("MagicMirror viewer APK is not packaged")
    packages = adb_command(host, "shell", "pm", "list", "packages", VIEWER_PACKAGE)
    installed = f"package:{VIEWER_PACKAGE}" in packages.splitlines()
    installed_version = None
    if installed:
        details = adb_command(host, "shell", "dumpsys", "package", VIEWER_PACKAGE)
        match = re.search(r"\bversionCode=(\d+)", details)
        installed_version = int(match.group(1)) if match else None
    if installed_version != VIEWER_VERSION_CODE:
        adb_command(host, "install", "-r", str(VIEWER_APK), timeout=35)


def launch_magicmirror_viewer(host):
    ensure_magicmirror_viewer(host)
    adb_command(host, "shell", "am", "start", "-n",
                f"{VIEWER_PACKAGE}/.ViewerActivity", "--es", "url", MAGICMIRROR_URL)


def installed_apps(tv_id, refresh=False):
    """Discover packages on this TV; never accept a package from the browser."""
    tvs = read_tvs()
    if tv_id not in tvs:
        raise ValueError("Unknown TV")
    if tvs[tv_id].get("type") == "webos":
        connection = controller.get(tv_id)
        future = asyncio.run_coroutine_threadsafe(_lg_apps(connection), controller.loop)
        return future.result(timeout=12)
    with APP_CACHE_LOCK:
        cached = APP_CACHE.get(tv_id)
        if cached and not refresh and time.monotonic() - cached[0] < 300:
            return cached[1]
    output = adb_command(tvs[tv_id]["host"], "shell", "pm", "list", "packages", timeout=8)
    packages = {
        line[8:].strip() for line in output.splitlines()
        if line.startswith("package:") and PACKAGE_RE.fullmatch(line[8:].strip())
    }
    known = {package: (id_, name) for id_, (name, package) in APPS.items()}
    discovered = {}
    for package in sorted(packages):
        if package in known:
            id_, name = known[package]
        elif package.startswith(("com.android.", "com.google.android.", "android.")):
            continue
        else:
            id_ = package
            name = package.rsplit(".", 1)[-1].replace("_", " ").title()
        discovered[id_] = {"name": name, "package": package}
    with APP_CACHE_LOCK:
        APP_CACHE[tv_id] = (time.monotonic(), discovered)
    return discovered


async def _lg_apps(connection):
    await connection.connect()
    entries = await connection.remote.get_apps() or []
    return {entry["id"]: {"name": entry.get("title") or entry["id"], "package": entry["id"]}
            for entry in entries if isinstance(entry, dict) and isinstance(entry.get("id"), str)}

def read_tvs():
    data = json.loads(CONFIG.read_text())
    return {
        k: v for k, v in data.items()
        if isinstance(v, dict)
        and all(x in v for x in ("name", "host"))
        and (all(x in v for x in ("cert", "key")) if v.get("type", "android") == "android" else bool(v.get("client_key")))
    }

def safe_file(name):
    path = (BASE / name).resolve()
    if not path.is_relative_to(BASE):
        raise ValueError("Invalid credential path")
    return str(path)

class TVConnection:
    def __init__(self, tv_id, config, loop):
        self.id = tv_id
        self.config = config
        self.loop = loop
        self.remote = None
        self.connected = False
        self.tv_on = None
        self.current_app = None
        self.volume = None
        self.last_error = None
        self.last_latency_ms = None
        self.last_command = None
        self.last_success = None
        self.lock = asyncio.Lock()
        self.last_adb_refresh = 0

    def availability(self, value):
        self.connected = bool(value)
        if not self.connected:
            self.tv_on = None
            self.current_app = None
            self.volume = None

    def power(self, value):
        self.tv_on = bool(value)

    def app_changed(self, value):
        self.current_app = value

    def volume_changed(self, value):
        try:
            self.volume = {
                "level": value.get("level") if isinstance(value, dict) else getattr(value, "level", None),
                "maximum": value.get("max") if isinstance(value, dict) else getattr(value, "max", None),
                "muted": value.get("muted") if isinstance(value, dict) else getattr(value, "muted", None)
            }
        except Exception:
            self.volume = None

    async def connect(self):
        if self.connected and self.remote is not None:
            return

        if self.remote is not None:
            # Allow the existing background reconnection to recover.
            for _ in range(30):
                if self.connected:
                    return
                await asyncio.sleep(0.2)

            raise ConnectionError(
                "TV is reconnecting; please try again"
            )

        self.connected = False
        remote = AndroidTVRemote(
            client_name="CarlPi Remote",
            host=self.config["host"],
            certfile=safe_file(self.config["cert"]),
            keyfile=safe_file(self.config["key"]),
        )

        remote.add_is_available_updated_callback(self.availability)
        remote.add_is_on_updated_callback(self.power)
        remote.add_current_app_updated_callback(self.app_changed)
        remote.add_volume_info_updated_callback(self.volume_changed)

        self.remote = remote
        try:
            await asyncio.wait_for(remote.async_connect(), timeout=5)
            self.connected = True
            if remote.is_on is not None:
                self.power(remote.is_on)
            if remote.current_app:
                self.app_changed(remote.current_app)
            if remote.volume_info:
                self.volume_changed(remote.volume_info)
            remote.keep_reconnecting()
            self.last_error = None
        except Exception:
            remote.disconnect()
            self.remote = None
            self.connected = False
            raise

    async def execute(self, command, value=None):
        async with self.lock:
            started = time.monotonic()
            for attempt in range(2):
                try:
                    await self.connect()

                    if command == "key":
                        self.remote.send_key_command(value)
                    elif command == "power_start":
                        self.remote.send_key_command("POWER", "START_LONG")

                    elif command == "power_end":
                        self.remote.send_key_command("POWER", "END_LONG")

                    elif command == "text":
                        self.remote.send_key_command("text:" + value)

                    elif command == "app":
                        method = getattr(
                            self.remote,
                            "send_launch_app_command", None
                        )
                        if not callable(method):
                            raise NotImplementedError(
                                "Direct app launching is unavailable"
                            )
                        method(value)

                    elif command == "volume":
                        await asyncio.to_thread(adb_command, self.config["host"],
                                                "shell", "cmd", "media_session", "volume",
                                                "--stream", "3", "--set", str(value))
                        self.volume = None

                    elif command == "magicmirror":
                        try:
                            await asyncio.to_thread(launch_magicmirror_viewer, self.config["host"])
                        except (subprocess.SubprocessError, OSError, TimeoutError) as exc:
                            app.logger.warning("Viewer launch unavailable (%s); using TV browser", type(exc).__name__)
                            method = getattr(self.remote, "send_launch_app_command", None)
                            if not callable(method):
                                raise NotImplementedError("Browser launching is unavailable") from exc
                            method(MAGICMIRROR_URL)

                    else:
                        raise ValueError("Unknown command")

                    # Let the asynchronous transport flush its queue.
                    await asyncio.sleep(0.08)
                    self.last_command = command
                    self.last_success = time.time()
                    self.last_latency_ms = round(
                        (time.monotonic() - started) * 1000
                    )
                    self.last_error = None
                    return

                except (NotImplementedError, ValueError):
                    raise
                except Exception as exc:
                    self.last_error = type(exc).__name__

                    if attempt == 1:
                        raise

                    await asyncio.sleep(0.2)

    def status(self):
        if self.connected and (self.current_app is None or self.volume is None or
                               time.monotonic() - self.last_adb_refresh > 10):
            self.last_adb_refresh = time.monotonic()
            live = adb_live_status(self.config["host"])
            self.current_app = live.get("current_app", self.current_app)
            self.volume = live.get("volume", self.volume)
        return {
            "connected": self.connected,
            "power": self.tv_on if self.connected else None,
            "current_app": self.current_app if self.connected else None,
            "volume": self.volume if self.connected else None,
            "latency_ms": self.last_latency_ms,
            "last_command": self.last_command,
            "last_success": self.last_success,
            "last_error": self.last_error,
        }


LG_KEYS = {
    "DPAD_UP": "UP", "DPAD_DOWN": "DOWN", "DPAD_LEFT": "LEFT", "DPAD_RIGHT": "RIGHT",
    "DPAD_CENTER": "ENTER", "HOME": "HOME", "BACK": "BACK", "MENU": "MENU",
    "ENTER": "ENTER", "TV_INPUT": "INPUT_HUB", "SETTINGS": "SETTINGS",
    "MEDIA_PLAY_PAUSE": "PLAY", "MEDIA_PREVIOUS": "REWIND", "MEDIA_NEXT": "FASTFORWARD",
    "MEDIA_REWIND": "REWIND", "MEDIA_FAST_FORWARD": "FASTFORWARD",
}


class LGConnection:
    def __init__(self, tv_id, config, loop):
        self.id, self.config, self.loop = tv_id, config, loop
        self.remote = None
        self.connected = False
        self.last_error = None
        self.last_latency_ms = None
        self.last_command = None
        self.last_success = None
        self.lock = asyncio.Lock()

    async def connect(self):
        if self.connected and self.remote:
            return
        from aiowebostv import WebOsClient
        client = WebOsClient(self.config["host"], self.config["client_key"])
        if not await asyncio.wait_for(client.connect(), timeout=10):
            raise ConnectionError("LG TV did not accept the connection")
        self.remote = client
        self.connected = True
        self.last_error = None

    async def execute(self, command, value=None):
        async with self.lock:
            started = time.monotonic()
            try:
                await self.connect()
                if command == "app":
                    await self.remote.launch_app(value)
                elif command == "volume":
                    await self.remote.set_volume(value)
                elif command == "magicmirror":
                    await self.remote.request("system.launcher/open", {"target": MAGICMIRROR_URL})
                elif command == "text":
                    await self.remote.request("com.webos.service.ime/insertText", {"text": value})
                elif command == "power_start":
                    await self.remote.power_off()
                elif command == "power_end":
                    pass
                elif command == "key":
                    if value == "POWER":
                        await self.remote.power_off()
                    elif value == "VOLUME_UP":
                        await self.remote.volume_up()
                    elif value == "VOLUME_DOWN":
                        await self.remote.volume_down()
                    elif value == "VOLUME_MUTE":
                        await self.remote.set_mute(not self.remote.tv_state.muted)
                    elif value == "DEL":
                        await self.remote.request("com.webos.service.ime/deleteCharacters", {"count": 1})
                    elif value == "ENTER":
                        await self.remote.request("com.webos.service.ime/sendEnterKey")
                    elif value == "MEDIA_PLAY_PAUSE":
                        await self.remote.play()
                    elif value == "MEDIA_REWIND":
                        await self.remote.rewind()
                    elif value == "MEDIA_FAST_FORWARD":
                        await self.remote.fast_forward()
                    elif value in LG_KEYS:
                        await self.remote.button(LG_KEYS[value])
                    else:
                        raise NotImplementedError("This control is unavailable on LG webOS")
                else:
                    raise NotImplementedError("This control is unavailable on LG webOS")
                self.last_command = command
                self.last_success = time.time()
                self.last_latency_ms = round((time.monotonic() - started) * 1000)
                self.last_error = None
            except (NotImplementedError, ValueError):
                raise
            except Exception as exc:
                self.connected = False
                self.remote = None
                self.last_error = type(exc).__name__
                raise

    def status(self):
        state = self.remote.tv_state if self.connected and self.remote else None
        return {"connected": self.connected, "power": state.is_on if state else None,
                "current_app": state.current_app_id if state else None,
                "volume": {"level": state.volume, "maximum": None, "muted": state.muted} if state else None,
                "latency_ms": self.last_latency_ms, "last_command": self.last_command,
                "last_success": self.last_success, "last_error": self.last_error}

class Controller:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.connections = {}
        self.thread = threading.Thread(
            target=self.run_loop, daemon=True
        )
        self.thread.start()

    def run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def get(self, tv_id):
        tvs = read_tvs()
        if tv_id not in tvs:
            raise ValueError("Unknown TV")

        existing = self.connections.get(tv_id)
        if existing and existing.config == tvs[tv_id]:
            return existing

        if existing:
            if existing.remote:
                if isinstance(existing, LGConnection):
                    asyncio.run_coroutine_threadsafe(existing.remote.disconnect(), self.loop)
                else:
                    self.loop.call_soon_threadsafe(existing.remote.disconnect)

        connection_type = LGConnection if tvs[tv_id].get("type") == "webos" else TVConnection
        connection = connection_type(tv_id, tvs[tv_id], self.loop)
        self.connections[tv_id] = connection
        return connection

    def send(self, tv_id, command, value):
        connection = self.get(tv_id)
        future = asyncio.run_coroutine_threadsafe(
            connection.execute(command, value),
            self.loop
        )
        try:
            future.result(timeout=50 if command == "magicmirror" else 13)
        except FutureTimeout:
            future.cancel()
            raise TimeoutError("TV connection timed out")

controller = Controller()

async def recover_saved_tvs():
    """Find a saved Android TV at a new IP using its existing pairing certificate."""
    async with RECOVERY_LOCK:
        tvs = read_tvs()
        missing = [tv_id for tv_id, config in reversed(list(tvs.items()))
                   if config.get("type", "android") == "android"
                   and not controller.get(tv_id).connected]
        if not missing:
            return []
        candidates = await asyncio.to_thread(discover_android, 2,
                                             [tvs[tv_id]["host"] for tv_id in missing])
        occupied = {tvs[tv_id]["host"] for tv_id in tvs
                    if controller.get(tv_id).connected}
        recovered = []
        for tv_id in missing:
            connection = controller.get(tv_id)
            if connection.connected:
                continue
            for candidate in candidates:
                host = candidate["host"]
                if host == connection.config["host"] or host in occupied:
                    continue
                probe = AndroidTVRemote(client_name="CarlPi Remote", host=host,
                                        certfile=safe_file(connection.config["cert"]),
                                        keyfile=safe_file(connection.config["key"]))
                try:
                    await asyncio.wait_for(probe.async_connect(), timeout=5)
                except Exception:
                    probe.disconnect()
                    continue
                probe.disconnect()
                if connection.remote is not None:
                    connection.remote.disconnect()
                    connection.remote = None
                connection.connected = False
                with CONFIG_LOCK:
                    config_data = json.loads(CONFIG.read_text())
                    if tv_id not in config_data:
                        break
                    config_data[tv_id]["host"] = host
                    temp = CONFIG.with_name(CONFIG.name + ".recovery")
                    temp.write_text(json.dumps(config_data, indent=2))
                    os.chmod(temp, 0o600)
                    os.replace(temp, CONFIG)
                connection.config["host"] = host
                occupied.add(host)
                recovered.append(tv_id)
                try:
                    await connection.connect()
                except Exception as exc:
                    connection.last_error = type(exc).__name__
                break
        return recovered

async def connect_on_startup(connection):
    while True:
        try:
            if not connection.connected:
                await connection.connect()
            await asyncio.sleep(10)
        except Exception as exc:
            connection.last_error = type(exc).__name__
            await asyncio.sleep(10)

for tv_id in read_tvs():
    connection = controller.get(tv_id)
    asyncio.run_coroutine_threadsafe(
        connect_on_startup(connection),
        controller.loop
    )

def start_tv_connection(tv_id):
    connection = controller.get(tv_id)
    asyncio.run_coroutine_threadsafe(connect_on_startup(connection), controller.loop)

async def recover_saved_tvs_periodically():
    while True:
        await asyncio.sleep(120)
        try:
            await recover_saved_tvs()
        except Exception as exc:
            app.logger.warning("Saved TV recovery unavailable: %s", type(exc).__name__)

asyncio.run_coroutine_threadsafe(recover_saved_tvs_periodically(), controller.loop)
failures = {}
failure_lock = threading.Lock()

def authenticated():
    return session.get("authenticated") is True and session.get("role") != "guest"


def guest_session_valid():
    if session.get("role") != "guest" or not GUEST_ACCESS.exists():
        return False
    try:
        data = json.loads(GUEST_ACCESS.read_text())
        return time.time() < data["expires"] and secrets.compare_digest(
            session.get("guest_id", ""), data["id"]
        )
    except (OSError, ValueError, KeyError):
        return False


def can_control():
    return authenticated() or guest_session_valid()


def guest_pin_matches(pin):
    if not isinstance(pin, str) or not GUEST_ACCESS.exists():
        return None
    try:
        data = json.loads(GUEST_ACCESS.read_text())
        if time.time() >= data["expires"]:
            return None
        candidate = hashlib.scrypt(pin.encode(), salt=bytes.fromhex(data["salt"]),
                                    n=16384, r=8, p=1).hex()
        return data if secrets.compare_digest(candidate, data["hash"]) else None
    except (OSError, ValueError, KeyError):
        return None



def check_origin():
    origin = request.headers.get("Origin")
    return not origin or origin == request.host_url.rstrip("/")

def valid_post():
    return authenticated() and check_origin() and request.headers.get("X-CSRF-Token") == session.get("csrf")

@app.get("/")
def index():
    if guest_session_valid():
        return render_template("guest.html", app_version=APP_VERSION)
    if not authenticated():
        return render_template("login.html")
    return render_template("index.html", app_version=APP_VERSION)

@app.post("/login")
def login():
    ip = request.remote_addr or "unknown"
    now = time.monotonic()

    with failure_lock:
        attempts = [
            x for x in failures.get(ip, [])
            if now - x < 300
        ]
        if len(attempts) >= 5:
            failures[ip] = attempts
            return "Too many attempts. Retry in five minutes.", 429

        supplied = request.form.get("pin", "")
        is_owner = secrets.compare_digest(supplied, PIN)
        guest = None if is_owner else guest_pin_matches(supplied)
        if not is_owner and guest is None:
            attempts.append(now)
            failures[ip] = attempts
            return "Incorrect PIN. Go back and try again.", 401

        failures.pop(ip, None)

    session.clear()
    session.permanent = is_owner
    session["authenticated"] = True
    session["role"] = "owner" if is_owner else "guest"
    if guest is not None:
        session["guest_id"] = guest["id"]
    session["csrf"] = secrets.token_urlsafe(24)
    return redirect("/")

@app.get("/logout")
def logout():
    session.clear()
    return redirect("/")


@app.get("/api/guest")
def guest_status():
    if not authenticated():
        return jsonify(error="Unauthorized"), 401
    if not GUEST_ACCESS.exists():
        return jsonify(active=False)
    try:
        data = json.loads(GUEST_ACCESS.read_text())
        return jsonify(active=time.time() < data["expires"], expires=data["expires"])
    except (OSError, ValueError, KeyError):
        return jsonify(active=False)


@app.post("/api/guest")
def create_guest():
    if not valid_post():
        return jsonify(error="Unauthorized or invalid session"), 403
    data = request.get_json(silent=True) or {}
    hours = data.get("hours", 8)
    if not isinstance(hours, int) or isinstance(hours, bool) or not 1 <= hours <= 72:
        return jsonify(error="Choose 1–72 hours"), 400
    pin = f"{secrets.randbelow(1_000_000):06d}"
    while secrets.compare_digest(pin, PIN):
        pin = f"{secrets.randbelow(1_000_000):06d}"
    salt = secrets.token_bytes(16)
    record = {"id": secrets.token_urlsafe(18), "salt": salt.hex(),
              "hash": hashlib.scrypt(pin.encode(), salt=salt, n=16384, r=8, p=1).hex(),
              "expires": time.time() + hours * 3600}
    with GUEST_LOCK:
        temp = GUEST_ACCESS.with_name(GUEST_ACCESS.name + ".new")
        temp.write_text(json.dumps(record))
        os.chmod(temp, 0o600)
        os.replace(temp, GUEST_ACCESS)
    return jsonify(pin=pin, expires=record["expires"])


@app.delete("/api/guest")
def revoke_guest():
    if not valid_post():
        return jsonify(error="Unauthorized or invalid session"), 403
    with GUEST_LOCK:
        GUEST_ACCESS.unlink(missing_ok=True)
    return jsonify(ok=True)

@app.get("/api/bootstrap")
def bootstrap():
    if not can_control():
        return jsonify(error="Unauthorized"), 401
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)

    if session.get("role") == "guest":
        return jsonify(version=APP_VERSION, csrf=session["csrf"], guest=True,
                       tvs={k: {"name": v["name"]} for k, v in read_tvs().items()})

    return jsonify(
        version=APP_VERSION,
        csrf=session["csrf"],
        tvs={
            k: {"name": v["name"], "type": v.get("type", "android")}
            for k, v in read_tvs().items()
        },
        preferences=read_preferences(),
        apps={}
    )


@app.get("/api/preferences")
def get_preferences():
    if not authenticated():
        return jsonify(error="Unauthorized"), 401
    return jsonify(preferences=read_preferences())


@app.put("/api/preferences")
def update_preferences():
    if not valid_post():
        return jsonify(error="Unauthorized or invalid session"), 403
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="Invalid preferences"), 400
    try:
        saved = save_preferences(data.get("preferences"),
                                 only_if_missing=data.get("only_if_missing") is True)
        return jsonify(preferences=saved)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/scenes/run")
def run_scene():
    if not valid_post():
        return jsonify(error="Unauthorized or invalid session"), 403
    data = request.get_json(silent=True) or {}
    prefs = read_preferences() or {"scenes": []}
    scene = next((item for item in prefs.get("scenes", [])
                  if item["id"] == data.get("id")), None)
    if scene is None or scene["tv"] not in read_tvs():
        return jsonify(error="Scene unavailable"), 404
    tv_id = scene["tv"]
    app_id = scene["app"]
    if app_id and app_id != "magicmirror":
        try:
            available = installed_apps(tv_id)
        except (subprocess.SubprocessError, OSError, TimeoutError, ImportError):
            return jsonify(error="Could not check the scene app"), 503
        if app_id not in available:
            return jsonify(error="Scene app is not installed on this TV"), 400
        app_id = available[app_id]["package"]
    try:
        connection = controller.get(tv_id)
        if connection.status()["power"] is False:
            if read_tvs()[tv_id].get("type") == "webos":
                return jsonify(error="Turn on the LG TV before running this scene"), 409
            controller.send(tv_id, "key", "POWER")
            time.sleep(2)
        if scene["volume"] is not None:
            controller.send(tv_id, "volume", scene["volume"])
        if app_id:
            controller.send(tv_id, "magicmirror" if scene["app"] == "magicmirror" else "app",
                            None if scene["app"] == "magicmirror" else app_id)
        return jsonify(ok=True, tv=tv_id, name=scene["name"])
    except Exception as exc:
        app.logger.warning("Scene failed: %s", type(exc).__name__)
        return jsonify(error="Could not run this scene. Check the TV connection."), 503


def screensaver_settings(host):
    component = adb_command(host, "shell", "settings", "get", "secure", "screensaver_components")
    enabled = adb_command(host, "shell", "settings", "get", "secure", "screensaver_enabled")
    return component, enabled


@app.get("/api/screensaver/<tv_id>")
def screensaver_status(tv_id):
    if not authenticated():
        return jsonify(error="Unauthorized"), 401
    tvs = read_tvs()
    if tv_id not in tvs:
        return jsonify(error="Unknown TV"), 404
    if tvs[tv_id].get("type") == "webos":
        return jsonify(supported=False, enabled=False)
    try:
        component, enabled = screensaver_settings(tvs[tv_id]["host"])
        return jsonify(supported=True, enabled=(component == VIEWER_DREAM and enabled == "1"))
    except (subprocess.SubprocessError, OSError):
        return jsonify(error="Could not check TV screensaver"), 503


@app.post("/api/screensaver/<tv_id>")
def set_screensaver(tv_id):
    if not valid_post():
        return jsonify(error="Unauthorized or invalid session"), 403
    tvs = read_tvs()
    if tv_id not in tvs:
        return jsonify(error="Unknown TV"), 404
    if tvs[tv_id].get("type") == "webos":
        return jsonify(error="Android TV only"), 400
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get("enabled"), bool):
        return jsonify(error="Choose whether to enable the screensaver"), 400
    host = tvs[tv_id]["host"]
    try:
        with SCREENSAVER_LOCK:
            current, previous_enabled = screensaver_settings(host)
            backups = json.loads(SCREENSAVER_BACKUP.read_text()) if SCREENSAVER_BACKUP.exists() else {}
            if data["enabled"]:
                ensure_magicmirror_viewer(host)
                if current != VIEWER_DREAM:
                    backups[tv_id] = {"component": current, "enabled": previous_enabled}
                    temp = SCREENSAVER_BACKUP.with_name(SCREENSAVER_BACKUP.name + ".new")
                    temp.write_text(json.dumps(backups, indent=2))
                    os.chmod(temp, 0o600)
                    os.replace(temp, SCREENSAVER_BACKUP)
                adb_command(host, "shell", "settings", "put", "secure", "screensaver_components", VIEWER_DREAM)
                adb_command(host, "shell", "settings", "put", "secure", "screensaver_enabled", "1")
            elif current == VIEWER_DREAM:
                previous = backups.pop(tv_id, None)
                if previous and previous["component"] != "null":
                    adb_command(host, "shell", "settings", "put", "secure", "screensaver_components", previous["component"])
                else:
                    adb_command(host, "shell", "settings", "delete", "secure", "screensaver_components")
                adb_command(host, "shell", "settings", "put", "secure", "screensaver_enabled",
                            previous["enabled"] if previous else "0")
                temp = SCREENSAVER_BACKUP.with_name(SCREENSAVER_BACKUP.name + ".new")
                temp.write_text(json.dumps(backups, indent=2))
                os.chmod(temp, 0o600)
                os.replace(temp, SCREENSAVER_BACKUP)
        return jsonify(ok=True, enabled=data["enabled"])
    except (subprocess.SubprocessError, OSError, ValueError, KeyError):
        app.logger.exception("TV screensaver update failed")
        return jsonify(error="Could not update TV screensaver"), 503


@app.get("/api/jack")
def jack_status():
    if not authenticated():
        return jsonify(error="Unauthorized"), 401
    state = JACK.read()
    try:
        JACK.rpc("session-get")
        jobs = JACK.jobs()
        available = True
    except (OSError, ValueError, RuntimeError, KeyError, TimeoutError):
        jobs = []
        available = False
    return jsonify(feeds=state["feeds"], tracked=state["tracked"], matches=state["matches"],
                   notices=state["notices"], indexers=INDEXERS,
                   completed=state["completed"], jobs=jobs,
                   client_available=available)


@app.post("/api/jack/notice/dismiss")
def jack_notice_dismiss():
    if not valid_post():
        return jsonify(error="Unauthorized or invalid session"), 403
    try:
        JACK.dismiss_notice((request.get_json(silent=True) or {}).get("key", ""))
        return jsonify(ok=True)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/jack/movie/delete")
def jack_movie_delete():
    if not valid_post():
        return jsonify(error="Unauthorized or invalid session"), 403
    try:
        count = JACK.delete_imported_movie((request.get_json(silent=True) or {}).get("source_ref", ""))
        return jsonify(ok=True, files=count)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except OSError:
        return jsonify(error="Could not remove movie files"), 503


@app.get("/api/jack/search")
def jack_search():
    if not authenticated():
        return jsonify(error="Unauthorized"), 401
    try:
        return jsonify(JACK.discover(request.args.get("q", "")[:80],
                                     request.args.get("quality", "any"),
                                     request.args.get("source", "all")))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/jack/feed")
def jack_feed():
    if not valid_post():
        return jsonify(error="Unauthorized or invalid session"), 403
    data = request.get_json(silent=True) or {}
    try:
        feeds = JACK.add_feed(data.get("url", ""))
        threading.Thread(target=jack_refresh_matches_safely, daemon=True).start()
        return jsonify(feeds=feeds)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except (OSError, TimeoutError):
        return jsonify(error="Could not read that feed"), 503


@app.post("/api/jack/track")
def jack_track():
    if not valid_post():
        return jsonify(error="Unauthorized or invalid session"), 403
    data = request.get_json(silent=True) or {}
    try:
        tracked = JACK.track(data.get("title", ""), data.get("category", "series"))
        threading.Thread(target=jack_refresh_matches_safely, daemon=True).start()
        return jsonify(tracked=tracked)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/jack/download")
def jack_download():
    if not valid_post():
        return jsonify(error="Unauthorized or invalid session"), 403
    data = request.get_json(silent=True) or {}
    try:
        job_id = JACK.add(data.get("source"), data.get("category"), data.get("quality"),
                          title=data.get("title"), identifier=data.get("id"), url=data.get("url"))
        return jsonify(ok=True, id=job_id)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except (OSError, RuntimeError, KeyError, TimeoutError) as exc:
        app.logger.warning("Jack Sparrow could not add download: %s", type(exc).__name__)
        return jsonify(error="Torrent client or source unavailable"), 503


@app.post("/api/jack/manage")
def jack_manage():
    if not valid_post():
        return jsonify(error="Unauthorized or invalid session"), 403
    data = request.get_json(silent=True) or {}
    try:
        JACK.manage(data.get("id"), data.get("action"))
        return jsonify(ok=True)
    except (ValueError, TypeError) as exc:
        return jsonify(error=str(exc)), 400
    except (OSError, RuntimeError, KeyError, TimeoutError):
        return jsonify(error="Torrent client unavailable"), 503


@app.post("/api/jack/remove-feed")
def jack_remove_feed():
    if not valid_post():
        return jsonify(error="Unauthorized or invalid session"), 403
    data = request.get_json(silent=True) or {}
    return jsonify(feeds=JACK.remove_feed(data.get("url", "")))


@app.post("/api/jack/untrack")
def jack_untrack():
    if not valid_post():
        return jsonify(error="Unauthorized or invalid session"), 403
    data = request.get_json(silent=True) or {}
    return jsonify(tracked=JACK.untrack(data.get("title", ""), data.get("category", "")))


@app.post("/api/jack/match-seen")
def jack_match_seen():
    if not valid_post():
        return jsonify(error="Unauthorized or invalid session"), 403
    data = request.get_json(silent=True) or {}
    try:
        JACK.mark_match_seen(data.get("key", ""))
        return jsonify(ok=True)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.get("/api/apps/<tv_id>")
def apps_for_tv(tv_id):
    if not authenticated():
        return jsonify(error="Unauthorized"), 401
    try:
        return jsonify(apps=installed_apps(tv_id, refresh=request.args.get("refresh") == "1"))
    except ValueError:
        return jsonify(error="Unknown TV"), 404
    except (subprocess.SubprocessError, OSError, TimeoutError, ImportError):
        return jsonify(error="Installed apps unavailable"), 503


@app.get("/api/tvs/discover")
def discover_tvs():
    if not authenticated():
        return jsonify(error="Unauthorized"), 401
    try:
        devices = discover_android() + discover_webos()
        paired = {v["host"] for v in read_tvs().values()}
        return jsonify(devices=[item for item in devices if item["host"] not in paired])
    except RuntimeError as exc:
        return jsonify(error=str(exc)), 503


@app.post("/api/tvs/reconnect")
def reconnect_saved_tvs():
    if not valid_post():
        return jsonify(error="Unauthorized or invalid session"), 403
    future = asyncio.run_coroutine_threadsafe(recover_saved_tvs(), controller.loop)
    try:
        recovered = future.result(timeout=45)
        return jsonify(recovered=recovered,
                       message=("Saved TV connection restored" if recovered else
                                "No saved TV moved to a new address"))
    except FutureTimeout:
        future.cancel()
        return jsonify(error="TV search timed out"), 503
    except Exception:
        app.logger.exception("Saved TV recovery failed")
        return jsonify(error="Could not search for saved TVs"), 503


@app.post("/api/tvs/pair/start")
def pair_start():
    if not valid_post():
        return jsonify(error="Unauthorized or invalid session"), 403
    data = request.get_json(silent=True) or {}
    try:
        tv_id = data.get("id", "")
        if tv_id in read_tvs():
            raise ValueError("That TV name is already in use")
        name = data.get("name", "")
        token, step = start_pairing(BASE, controller.loop, data.get("type"), data.get("host"), name.strip() if isinstance(name, str) else name, tv_id)
        return jsonify(token=token, step=step)
    except (ValueError, RuntimeError) as exc:
        return jsonify(error=str(exc)), 400
    except Exception:
        app.logger.exception("TV pairing failed to start")
        return jsonify(error="Could not contact the TV. Check its IP and network settings."), 503


@app.get("/api/tvs/pair/<token>")
def pair_status(token):
    if not authenticated():
        return jsonify(error="Unauthorized"), 401
    try:
        return jsonify(pairing_state(token))
    except ValueError as exc:
        return jsonify(error=str(exc)), 404


@app.post("/api/tvs/pair/finish")
def pair_finish():
    if not valid_post():
        return jsonify(error="Unauthorized or invalid session"), 403
    data = request.get_json(silent=True) or {}
    try:
        with CONFIG_LOCK:
            tv_id = finish_pairing(BASE, CONFIG, controller.loop, data.get("token"), data.get("code"))
        start_tv_connection(tv_id)
        return jsonify(ok=True, tv_id=tv_id, name=read_tvs()[tv_id]["name"])
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except Exception:
        app.logger.exception("TV pairing failed")
        return jsonify(error="Pairing failed. Check the TV prompt or code and try again."), 503

@app.get("/api/status/<tv_id>")
def status(tv_id):
    if not can_control():
        return jsonify(error="Unauthorized"), 401
    try:
        return jsonify(controller.get(tv_id).status())
    except ValueError:
        return jsonify(error="Unknown TV"), 404
@app.get("/api/device-info/<tv_id>")
def device_info(tv_id):
    if not authenticated():
        return jsonify(error="Unauthorized"), 401

    tvs = read_tvs()
    if tv_id not in tvs:
        return jsonify(error="Unknown TV"), 404
    if tvs[tv_id].get("type") == "webos":
        return jsonify(error="Hardware diagnostics are unavailable for LG webOS"), 501

    host = tvs[tv_id]["host"]
    def adb_read(*args):
        return adb_command(host, "shell", *args, timeout=4)

    try:
        meminfo = adb_read("cat", "/proc/meminfo")
        memory = {}
        for line in meminfo.splitlines():
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            if name in ("MemTotal", "MemAvailable"):
                memory[name] = int(value.strip().split()[0]) * 1024

        storage_output = adb_read("df", "-k", "/data")
        storage_lines = storage_output.splitlines()
        if len(storage_lines) < 2:
            raise ValueError("Storage information unavailable")

        fields = storage_lines[-1].split()
        storage_total = int(fields[1]) * 1024
        storage_used = int(fields[2]) * 1024
        storage_available = int(fields[3]) * 1024

        return jsonify(
            ram={
                "total": memory.get("MemTotal"),
                "available": memory.get("MemAvailable"),
            },
            storage={
                "total": storage_total,
                "used": storage_used,
                "available": storage_available,
            },
            android=adb_read("getprop", "ro.build.version.release"),
            model=adb_read("getprop", "ro.product.model"),
        )

    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        app.logger.warning(
            "TV diagnostics unavailable: %s", type(exc).__name__
        )
        return jsonify(
            error="TV diagnostics unavailable",
            adb_connected=False,
        ), 503

@app.post("/api/command")
def command():
    if not can_control():
        return jsonify(error="Unauthorized"), 401
    if not check_origin():
        return jsonify(error="Invalid origin"), 403
    if request.headers.get("X-CSRF-Token") != session.get("csrf"):
        return jsonify(error="Invalid session token"), 403

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="Invalid JSON"), 400

    tv_id = data.get("tv")
    kind = data.get("type")
    value = data.get("value")

    if not isinstance(tv_id, str) or tv_id not in read_tvs():
        return jsonify(error="Unknown TV"), 400

    if session.get("role") == "guest" and (kind != "key" or value not in {
        "volup", "voldown", "mute", "play", "previous", "next", "rewind", "forward",
        "up", "down", "left", "right", "ok", "back", "home"
    }):
        return jsonify(error="Guest remote cannot use this control"), 403

    if kind == "key":
        if value not in KEYS:
            return jsonify(error="Unknown key"), 400
        value = KEYS[value]
    elif kind in ("power_start", "power_end"):
        if value != "power":
            return jsonify(error="Invalid power command"), 400
        value = "POWER"
    elif kind == "text":
        if not isinstance(value, str) or not 1 <= len(value) <= 200:
            return jsonify(error="Text must be 1–200 characters"), 400
        if any(ord(c) < 32 for c in value):
            return jsonify(error="Control characters not allowed"), 400

    elif kind == "magicmirror":
        value = None
    elif kind == "app":
        if not isinstance(value, str):
            return jsonify(error="Unknown app"), 400
        try:
            available = installed_apps(tv_id)
        except (subprocess.SubprocessError, OSError, TimeoutError, ImportError):
            return jsonify(error="Installed apps unavailable"), 503
        if value not in available:
            return jsonify(error="Unknown app"), 400
        value = available[value]["package"]

    else:
        return jsonify(error="Unknown command type"), 400

    try:
        controller.send(tv_id, kind, value)
        return jsonify(ok=True)
    except NotImplementedError as exc:
        return jsonify(error=str(exc)), 501
    except Exception as exc:
        app.logger.exception("Remote command failed")
        return jsonify(
            error="TV unavailable or command failed",
            detail=type(exc).__name__
        ), 503

def jack_import_loop():
    next_search = 0
    while True:
        try:
            JACK.jobs()
        except (OSError, RuntimeError, ValueError, KeyError, TimeoutError) as exc:
            app.logger.debug("Jack Sparrow background check: %s", type(exc).__name__)
        if time.monotonic() >= next_search:
            jack_refresh_matches_safely()
            try:
                JACK.refresh_plex_notices()
            except (OSError, RuntimeError, ValueError, KeyError, TimeoutError, sqlite3.Error) as exc:
                app.logger.debug("Jack Sparrow Plex check: %s", type(exc).__name__)
            next_search = time.monotonic() + 600
        time.sleep(60)


def jack_refresh_matches_safely():
    try:
        JACK.refresh_matches()
    except (OSError, RuntimeError, ValueError, KeyError, TimeoutError) as exc:
        app.logger.debug("Jack Sparrow release check: %s", type(exc).__name__)


if __name__ == "__main__":
    threading.Thread(target=jack_import_loop, name="jack-sparrow-import", daemon=True).start()
    print(f"TCL Remote v2: http://192.168.1.21:{PORT}")
    serve(app, host="0.0.0.0", port=PORT, threads=8)

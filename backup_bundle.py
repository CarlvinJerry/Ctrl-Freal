"""Password-protected portable Ctrl Freak settings bundle."""

import base64
import hashlib
import json
import os
import re
from pathlib import Path


def _cipher(password, salt):
    from cryptography.fernet import Fernet
    key = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
    return Fernet(base64.urlsafe_b64encode(key))


def seal(data, password):
    if len(password) < 12:
        raise ValueError("Use a backup password of at least 12 characters")
    salt = os.urandom(16)
    payload = json.dumps(data, separators=(",", ":")).encode("utf-8")
    if len(payload) > 5_000_000:
        raise ValueError("Backup is too large")
    return b"CFB1" + salt + _cipher(password, salt).encrypt(payload)


def unseal(blob, password):
    if not blob.startswith(b"CFB1") or len(blob) > 7_000_000:
        raise ValueError("Invalid Ctrl Freak backup")
    from cryptography.fernet import InvalidToken
    try:
        data = json.loads(_cipher(password, blob[4:20]).decrypt(blob[20:]))
    except (ValueError, TypeError, InvalidToken) as exc:
        raise ValueError("Incorrect password or damaged backup") from exc
    if not isinstance(data, dict) or data.get("format") != 1:
        raise ValueError("Unsupported Ctrl Freak backup")
    return data


def credential_names(tvs):
    names = set()
    for tv in tvs.values():
        if tv.get("type", "android") == "android":
            names.update([tv.get("cert"), tv.get("key")])
    if any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+\.pem", name)
           for name in names):
        raise ValueError("Invalid TV credential name")
    return names


def make_bundle(base, tvs, preferences, jack):
    files = {}
    for name in credential_names(tvs):
        content = (Path(base) / name).read_bytes()
        if len(content) > 100_000:
            raise ValueError("TV credential is too large")
        files[name] = base64.b64encode(content).decode("ascii")
    backgrounds = {}
    for tv_id in tvs:
        image = Path(base) / ".backgrounds" / f"{tv_id}.jpg"
        if image.is_file() and image.stat().st_size < 3_000_000:
            backgrounds[tv_id] = base64.b64encode(image.read_bytes()).decode("ascii")
    return {"format": 1, "tvs": tvs, "credentials": files,
            "backgrounds": backgrounds, "preferences": preferences,
            "jack": {"feeds": jack.get("feeds", []), "tracked": jack.get("tracked", [])}}


import asyncio
import json
import re
import sys
from pathlib import Path
from androidtvremote2 import AndroidTVRemote

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "tvs.json"

async def main():
    if len(sys.argv) != 4:
        print("Usage: python add_tv.py ID IP 'Display Name'")
        return

    tv_id, host, name = sys.argv[1:]
    if not re.fullmatch(r"[a-z0-9_-]{1,30}", tv_id):
        raise ValueError("Invalid TV ID")

    tvs = json.loads(CONFIG.read_text())
    if tv_id in tvs:
        raise ValueError("TV ID already exists")

    cert = f"{tv_id}_cert.pem"
    key = f"{tv_id}_key.pem"

    remote = AndroidTVRemote(
        client_name="CarlPi Remote",
        host=host,
        certfile=str(BASE / cert),
        keyfile=str(BASE / key),
    )

    try:
        await remote.async_generate_cert_if_missing()
        await remote.async_start_pairing()
        code = input("Enter TV pairing code: ").strip()
        await remote.async_finish_pairing(code)
        await remote.async_connect()
        print("TV successfully paired")
    finally:
        remote.disconnect()

    for filename in (cert, key):
        (BASE / filename).chmod(0o600)

    tvs[tv_id] = {
        "name": name,
        "host": host,
        "cert": cert,
        "key": key
    }
    CONFIG.write_text(json.dumps(tvs, indent=2))
    print("TV added. Restart the remote server to load it.")

asyncio.run(main())

"""Local TV discovery and one-time pairing helpers."""
import asyncio
import ipaddress
import json
import os
import re
import secrets
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from androidtvremote2 import AndroidTVRemote
from lg_wake import neighbor_mac

PAIRINGS = {}
PAIRING_LOCK = threading.Lock()
TV_ID = re.compile(r"^[a-z0-9_-]{1,30}$")


def valid_host(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError("Enter a valid TV IP address") from exc
    if address.version != 4 or not address.is_private or address.is_loopback or address.is_link_local:
        raise ValueError("Use a private IPv4 address on your home network")
    return str(address)


def save_tv(config_path, tv_id, details):
    if not TV_ID.fullmatch(tv_id):
        raise ValueError("Invalid TV ID")
    data = json.loads(config_path.read_text())
    if tv_id in data:
        raise ValueError("That TV name is already in use")
    data[tv_id] = details
    temp = config_path.with_name(config_path.name + ".new")
    temp.write_text(json.dumps(data, indent=2))
    os.chmod(temp, 0o600)
    os.replace(temp, config_path)


def scan_android_subnets(subnet_hosts=None):
    """Find Android TV Remote v2 listeners on a small number of private /24 networks."""
    hosts = list(subnet_hosts or [])
    if not hosts:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            try:
                sock.connect(("8.8.8.8", 80))
                hosts.append(sock.getsockname()[0])
            except OSError:
                return []
    networks = []
    for host in hosts:
        try:
            address = valid_host(host)
            network = ipaddress.ip_network(f"{address}/24", strict=False)
        except ValueError:
            continue
        if network not in networks:
            networks.append(network)
        if len(networks) >= 3:
            break

    def reachable(host):
        for port in (6466, 5555):
            try:
                with socket.create_connection((host, port), timeout=0.35):
                    return host
            except OSError:
                continue
        return None

    addresses = [str(ip) for network in networks for ip in network.hosts()]
    with ThreadPoolExecutor(max_workers=48) as pool:
        found = [host for host in pool.map(reachable, addresses) if host]
    return [{"name": f"Android TV at {host}", "host": host, "type": "android"}
            for host in found]


def discover_android(seconds=4, subnet_hosts=None):
    try:
        from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
    except ImportError as exc:
        raise RuntimeError("TV discovery requires zeroconf on the Pi") from exc
    found = {}
    lock = threading.Lock()
    service = "_androidtvremote2._tcp.local."

    class Listener(ServiceListener):
        def add_service(self, zc, type_, name):
            info = zc.get_service_info(type_, name, timeout=1000)
            if info:
                for ip in info.parsed_addresses():
                    try:
                        host = valid_host(ip)
                    except ValueError:
                        continue
                    with lock:
                        found[host] = {"name": name.split("._androidtvremote2")[0], "host": host, "type": "android"}

        def update_service(self, zc, type_, name):
            self.add_service(zc, type_, name)

        def remove_service(self, zc, type_, name):
            pass

    zc = Zeroconf()
    try:
        browser = ServiceBrowser(zc, service, Listener())
        time.sleep(seconds)
        browser.cancel()
    finally:
        zc.close()
    if found:
        return list(found.values())
    return scan_android_subnets(subnet_hosts)


def discover_webos(seconds=3):
    target = "urn:lge-com:service:webos-second-screen:1"
    packet = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
              'MAN: "ssdp:discover"\r\nMX: 2\r\nST: ' + target + "\r\n\r\n").encode()
    found = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.settimeout(0.4)
        sock.sendto(packet, ("239.255.255.250", 1900))
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                response, address = sock.recvfrom(8192)
            except socket.timeout:
                continue
            text = response.decode("utf-8", "ignore").lower()
            if target not in text:
                continue
            try:
                host = valid_host(address[0])
            except ValueError:
                continue
            found[host] = {"name": "LG webOS TV", "host": host, "type": "webos"}
    return list(found.values())


def start_pairing(base, loop, kind, host, name, tv_id):
    if not all(isinstance(value, str) for value in (kind, host, name, tv_id)):
        raise ValueError("Enter a TV type, IP address, and name")
    host = valid_host(host)
    if kind not in ("android", "webos"):
        raise ValueError("Choose Android TV or LG webOS")
    if not TV_ID.fullmatch(tv_id) or not name or len(name) > 60:
        raise ValueError("Enter a TV name using letters, numbers, hyphens or underscores")
    token = secrets.token_urlsafe(24)
    with PAIRING_LOCK:
        for old, item in list(PAIRINGS.items()):
            if time.monotonic() - item["created"] > 180:
                PAIRINGS.pop(old, None)
        PAIRINGS[token] = {"kind": kind, "host": host, "name": name, "id": tv_id,
                           "created": time.monotonic(), "state": "starting"}
    if kind == "android":
        future = asyncio.run_coroutine_threadsafe(_android_start(base, token), loop)
        try:
            future.result(timeout=12)
        except Exception:
            with PAIRING_LOCK:
                item = PAIRINGS.pop(token, None)
            if item and item.get("remote"):
                item["remote"].disconnect()
            for suffix in ("cert.pem", "key.pem"):
                (base / f".pairing_{token}_{suffix}").unlink(missing_ok=True)
            raise
        return token, "code"
    asyncio.run_coroutine_threadsafe(_webos_start(token), loop)
    return token, "approve"


async def _android_start(base, token):
    item = PAIRINGS[token]
    # Unique temporary credentials avoid touching an already paired TV.
    cert = base / f".pairing_{token}_cert.pem"
    key = base / f".pairing_{token}_key.pem"
    remote = AndroidTVRemote(client_name="Ctrl Freak", host=item["host"], certfile=str(cert), keyfile=str(key))
    item.update(remote=remote, cert=cert, key=key)
    await asyncio.wait_for(remote.async_generate_cert_if_missing(), timeout=8)
    await asyncio.wait_for(remote.async_start_pairing(), timeout=8)
    item["state"] = "code"


async def _webos_start(token):
    item = PAIRINGS[token]
    try:
        from aiowebostv import WebOsClient
        client = WebOsClient(item["host"], None)
        item["client"] = client
        if not await asyncio.wait_for(client.connect(), timeout=90):
            raise ConnectionError("LG TV did not approve pairing")
        item["client_key"] = client.client_key
        item["state"] = "paired"
    except Exception as exc:
        item["state"] = "error"
        item["error"] = type(exc).__name__


def pairing_state(token):
    if not isinstance(token, str):
        raise ValueError("Invalid pairing session")
    with PAIRING_LOCK:
        item = PAIRINGS.get(token)
        if not item:
            raise ValueError("Pairing session expired")
        return {"state": item["state"], "error": item.get("error")}


def finish_pairing(base, config_path, loop, token, code=None):
    if not isinstance(token, str):
        raise ValueError("Invalid pairing session")
    with PAIRING_LOCK:
        item = PAIRINGS.get(token)
    if not item or time.monotonic() - item["created"] > 180:
        raise ValueError("Pairing session expired")
    if item["kind"] == "android":
        if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9]{4,8}", code.strip()):
            raise ValueError("Enter the code shown on the TV")
        future = asyncio.run_coroutine_threadsafe(_android_finish(item, code.strip()), loop)
        future.result(timeout=15)
        cert_name = f"{item['id']}_cert.pem"
        key_name = f"{item['id']}_key.pem"
        if (base / cert_name).exists() or (base / key_name).exists():
            raise ValueError("Credential files already exist for that name")
        os.replace(item["cert"], base / cert_name)
        os.replace(item["key"], base / key_name)
        os.chmod(base / cert_name, 0o600)
        os.chmod(base / key_name, 0o600)
        details = {"name": item["name"], "host": item["host"], "type": "android", "cert": cert_name, "key": key_name}
    else:
        if item["state"] != "paired" or not item.get("client_key"):
            raise ValueError("Approve the request on the LG TV first")
        details = {"name": item["name"], "host": item["host"], "type": "webos", "client_key": item["client_key"]}
        mac = neighbor_mac(item["host"])
        if mac:
            details["mac"] = mac
        asyncio.run_coroutine_threadsafe(item["client"].disconnect(), loop).result(timeout=5)
    save_tv(config_path, item["id"], details)
    with PAIRING_LOCK:
        PAIRINGS.pop(token, None)
    return item["id"]


async def _android_finish(item, code):
    await asyncio.wait_for(item["remote"].async_finish_pairing(code), timeout=10)
    await asyncio.wait_for(item["remote"].async_connect(), timeout=8)
    item["remote"].disconnect()

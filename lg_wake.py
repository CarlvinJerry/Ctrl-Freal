"""LG webOS wake packet helpers for TVs that support Mobile TV On."""

import re
import socket
import subprocess

MAC_RE = re.compile(r"^(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")


def connection_mac(info):
    if not isinstance(info, dict):
        return None
    for key, value in info.items():
        if "mac" in key.lower() and isinstance(value, str) and MAC_RE.fullmatch(value):
            return value.lower()
        if isinstance(value, dict):
            found = connection_mac(value)
            if found:
                return found
    return None


def neighbor_mac(host):
    try:
        result = subprocess.run(["ip", "neigh", "show", host], capture_output=True,
                                text=True, timeout=3, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"\blladdr\s+((?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})\b", result.stdout)
    return match.group(1).lower() if match else None


def webos_available(host):
    try:
        with socket.create_connection((host, 3000), timeout=0.7):
            return True
    except OSError:
        return False


def wake(mac, host):
    if not isinstance(mac, str) or not MAC_RE.fullmatch(mac):
        raise ValueError("LG TV MAC address is unavailable")
    address = bytes.fromhex(mac.replace(":", ""))
    packet = b"\xff" * 6 + address * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        connection.settimeout(2)
        for target in (host, "255.255.255.255"):
            connection.sendto(packet, (target, 9))

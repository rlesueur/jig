"""Which of Jig's own networks the sandbox services sit on, read from the kernel (Linux only)."""

from __future__ import annotations

import ipaddress
import socket
import struct
from pathlib import Path

ROUTES = Path("/proc/net/route")


def _hex_ip(value: str) -> ipaddress.IPv4Address:
    return ipaddress.IPv4Address(socket.inet_ntoa(struct.pack("<I", int(value, 16))))


def connected_networks() -> list[tuple[str, ipaddress.IPv4Network]]:
    """Directly connected IPv4 networks (no gateway) with their interface, from /proc/net/route."""
    nets = []
    for line in ROUTES.read_text(encoding="ascii").splitlines()[1:]:
        fields = line.split()
        if len(fields) < 8 or int(fields[2], 16) != 0:
            continue
        dest, mask = _hex_ip(fields[1]), _hex_ip(fields[7])
        if int(mask) == 0:
            continue
        nets.append((fields[0], ipaddress.IPv4Network(f"{dest}/{mask}", strict=False)))
    return nets


def network_of(address: str) -> ipaddress.IPv4Network | None:
    ip = ipaddress.IPv4Address(address)
    for _iface, net in connected_networks():
        if ip in net:
            return net
    return None


def local_address_towards(address: str) -> str:
    """Jig's own address on the interface that reaches ``address`` (no packet is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((address, 9))
        return s.getsockname()[0]

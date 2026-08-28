"""Fail closed on external networking in explicitly guarded spawned test workers."""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import socket


def _is_loopback_host(host: object) -> bool:
    text = str(host).strip().lower()
    if text == "localhost":
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


if os.environ.get("QAGENT_TEST_NETWORK_GUARD") == "1":
    _original_connect = socket.socket.connect
    _original_getaddrinfo = socket.getaddrinfo

    def _guarded_connect(self, address):
        if self.family == socket.AF_UNIX:
            return _original_connect(self, address)
        host = address[0] if isinstance(address, tuple) and address else address
        if not _is_loopback_host(host):
            raise RuntimeError(f"pytest blocked external socket connection to {host}")
        return _original_connect(self, address)

    def _guarded_getaddrinfo(host, *args, **kwargs):
        if host is not None and not _is_loopback_host(host):
            raise RuntimeError(f"pytest blocked external DNS lookup for {host}")
        return _original_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = _guarded_connect
    socket.getaddrinfo = _guarded_getaddrinfo

    audit_file = os.environ.get("QAGENT_TEST_NETWORK_GUARD_AUDIT_FILE")
    if audit_file:
        with Path(audit_file).open("a", encoding="utf-8") as stream:
            stream.write(f"{os.getpid()}\n")

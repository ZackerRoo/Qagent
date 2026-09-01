#!/usr/bin/env python3
"""Safely merge proxy assignments from /etc/environment into qagent.env."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import tempfile
from pathlib import Path


PROXY_KEYS = (
    "http_proxy",
    "https_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "no_proxy",
    "NO_PROXY",
)
_ASSIGNMENT = re.compile(rf"^\s*(?:export\s+)?({'|'.join(PROXY_KEYS)})\s*=\s*(.*?)\s*$")
_GENERATED_COMMENT = "# Proxy settings copied safely from /etc/environment."


def _parse_value(raw: str, *, source: Path, line_number: int) -> str:
    if not raw:
        return ""
    lexer = shlex.shlex(raw, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = "#"
    try:
        tokens = list(lexer)
    except ValueError as error:
        raise ValueError(f"{source}:{line_number}: invalid proxy assignment") from error
    if len(tokens) != 1:
        raise ValueError(f"{source}:{line_number}: invalid proxy assignment")
    return tokens[0]


def read_proxy_assignments(source: Path) -> dict[str, str]:
    if not source.exists():
        return {}

    assignments: dict[str, str] = {}
    for line_number, line in enumerate(source.read_text().splitlines(), start=1):
        match = _ASSIGNMENT.match(line)
        if match:
            assignments[match.group(1)] = _parse_value(
                match.group(2), source=source, line_number=line_number
            )
    return assignments


def _with_loopback_bypass(value: str) -> str:
    entries = [entry.strip() for entry in value.split(",") if entry.strip()]
    for required in ("localhost", "127.0.0.1", "::1"):
        if required not in entries:
            entries.append(required)
    return ",".join(entries)


def merge_proxy_assignments(source: Path, target: Path) -> int:
    existing = target.read_text() if target.exists() else ""
    # Explicit service configuration wins over host defaults, key by key.
    proxies = read_proxy_assignments(source)
    proxies.update(read_proxy_assignments(target))
    for key in ("no_proxy", "NO_PROXY"):
        proxies[key] = _with_loopback_bypass(proxies.get(key, ""))
    preserved = [
        line
        for line in existing.splitlines()
        if not _ASSIGNMENT.match(line) and line != _GENERATED_COMMENT
    ]
    while preserved and not preserved[-1]:
        preserved.pop()

    merged = preserved
    if proxies:
        if merged:
            merged.append("")
        merged.append(_GENERATED_COMMENT)
        merged.extend(
            f"{key}={shlex.quote(proxies[key])}" for key in PROXY_KEYS if key in proxies
        )
    content = "\n".join(merged) + "\n"

    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", dir=target.parent
    )
    try:
        with os.fdopen(descriptor, "w") as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, target)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return len(proxies)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("/etc/environment"))
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    count = merge_proxy_assignments(args.source, args.target)
    print(f"merged {count} proxy environment variable(s); values were not displayed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

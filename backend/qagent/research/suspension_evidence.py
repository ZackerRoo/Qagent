"""Finite reviewed evidence for retrospective research price-gap classification."""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import re
from urllib.parse import urlparse

DEFAULT_EVIDENCE_PATH = Path(__file__).with_name("data") / "confirmed_suspensions.json"


def load_suspension_evidence(path: Path | None = None) -> dict[tuple[str, date], dict]:
    """Missing/invalid bundles never suppress retries; dates are never extrapolated."""
    try:
        with (path or DEFAULT_EVIDENCE_PATH).open("rb") as handle:
            raw = handle.read(262145)
        if len(raw) > 262144:
            return {}
        bundle = json.loads(raw)
        if bundle["schema_version"] != 1 or bundle["usage"] != "research_only_retrospective":
            return {}
        reviewed = date.fromisoformat(bundle["reviewed_on"])
        rows = bundle["entries"]
        if not isinstance(rows, list) or len(rows) > 1000:
            return {}
        result = {}
        for row in rows:
            instrument, dates, sources = row["instrument_id"], row["confirmed_dates"], row["sources"]
            if (not re.fullmatch(r"CN:\d{6}", instrument)
                    or row["status"] != "confirmed_suspended"
                    or not isinstance(dates, list) or not 1 <= len(dates) <= 366
                    or not isinstance(sources, list) or not sources):
                return {}
            for source in sources:
                url = urlparse(source["url"])
                published = date.fromisoformat(source["published_on"])
                if (url.scheme != "https" or not url.hostname or url.username or url.password
                        or not source["title"].strip() or published > reviewed):
                    return {}
            for value in dates:
                day = date.fromisoformat(value)
                key = (instrument, day)
                if day > reviewed or key in result:
                    return {}
                result[key] = row
        return result
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}

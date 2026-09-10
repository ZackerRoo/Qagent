"""Opt-in research-only capture of the unfiltered, finalized scan cohort."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import sqlite3
import tempfile

SOURCE_PROTOCOL = "g2-forward-source-v1"


def digest(value: dict) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def atomic_archive(path: Path, value: dict) -> bool:
    """Publish a complete file exclusively; never replace an existing archive."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".g2-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o444)
        try:
            os.link(temporary, path)
        except FileExistsError:
            return False
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return True
    finally:
        os.unlink(temporary)


def read_industries(db: Path, provider: str, signal_date: str, cutoff: datetime) -> dict:
    """Use the existing industry as-of ordering in one strictly read-only snapshot."""
    with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        revision = connection.execute(
            "SELECT revision, updated_at FROM historical_data_revisions WHERE provider_mode=?",
            (provider,),
        ).fetchone()
        if revision is None:
            raise ValueError("missing historical dataset revision")
        records = connection.execute(
            """SELECT * FROM historical_industry_snapshots
            WHERE provider_mode=? AND snapshot_date<=? AND dataset_revision<=?
            ORDER BY instrument_id,snapshot_date DESC,dataset_revision DESC,source_provider""",
            (provider, signal_date, revision["revision"]),
        ).fetchall()
        industries = {}
        for record in records:
            # SQLite legacy DateTime columns store naive UTC, unlike source JSON.
            fetched = datetime.fromisoformat(record["fetched_at"])
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)
            if fetched <= cutoff:
                industries.setdefault(record["instrument_id"], dict(record))
        return {"db_path": str(db.resolve()), "revision": dict(revision), "industries": industries}


def capture_source(*, output: Path, db: Path, provider: str, scan_job_id: str,
                   signal_date, rankings, stock_ids: set[str], items) -> Path:
    started = datetime.now(timezone.utc)
    source = read_industries(db, provider, signal_date.isoformat(), started)
    selected = [ranking for ranking in rankings if ranking.instrument_id in stock_ids]
    selected_ids = {ranking.instrument_id for ranking in selected}
    if len(selected_ids) != len(selected):
        raise ValueError("duplicate ranking identity")
    source["industries"] = {key: value for key, value in source["industries"].items() if key in selected_ids}
    root = Path(__file__).resolve().parents[1]
    source.update({
        "protocol": SOURCE_PROTOCOL, "stage": "ranking_finalized_before_job_completion",
        "provider": provider, "scan_job_id": scan_job_id, "signal_date": signal_date.isoformat(),
        "capture_started_at_utc": started.isoformat(),
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "rankings": [ranking.model_dump(mode="json") for ranking in selected],
        "items": [item.model_dump(mode="json") for item in items if item.instrument_id in selected_ids],
        "stock_ids": sorted(stock_ids), "research_universe": sorted(selected_ids),
        "source_sha256": {name: sha256((root / name).read_bytes()).hexdigest() for name in (
            "research/g2_forward_source.py", "jobs/full_market.py", "jobs/daily_scan.py", "factors/engine.py")},
        "decision_weight": False, "activation_allowed": False,
    })
    source["source_digest"] = digest(source)
    # A retry never overwrites the first capture of a scan; a new scan may repair missing inputs.
    name = sha256(scan_job_id.encode()).hexdigest()
    path = output / f"{signal_date.isoformat()}-{name}.json"
    atomic_archive(path, source)
    return path


def capture_if_enabled(**kwargs) -> Path | None:
    """Disabled by default; all capture failures leave the existing scan unchanged."""
    directory = os.environ.get("QAGENT_G2_CAPTURE_DIR")
    if not directory:
        return None
    try:
        from sqlalchemy.engine import make_url
        from qagent.config import get_settings

        url = make_url(get_settings().database_url)
        if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
            raise ValueError("G2 source capture requires a file-backed SQLite database")
        return capture_source(output=Path(directory), db=Path(url.database), **kwargs)
    except Exception:
        logging.getLogger(__name__).warning("G2 research source capture failed; existing scan continues")
        return None

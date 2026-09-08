import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "recent_dump_probe", Path(__file__).resolve().parents[2] / "scripts/probe_fuyao_recent_dump.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class Response:
    status_code = 200
    headers = {}
    chunks = [b"parquet"]
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def iter_content(self, chunk_size):
        return iter(self.chunks)


class Session:
    def __init__(self, response):
        self.response = response
    def get(self, url, **kwargs):
        assert kwargs == {"stream": True, "timeout": (5, 10), "allow_redirects": False}
        return self.response


def test_download_stream_and_no_auth_headers():
    assert probe.download(Session(Response()), "https://storage.example/file?secret=x") == b"parquet"


@pytest.mark.parametrize("declared", [True, False])
def test_download_bounds_declared_and_actual_bytes(monkeypatch, declared):
    monkeypatch.setattr(probe, "MAX_BYTES", 3)
    response = Response()
    response.headers = {"Content-Length": "4"} if declared else {}
    response.chunks = [b"ab", b"cd"]
    with pytest.raises(probe.ProbeError, match="download_size_limit"):
        probe.download(Session(response), "https://storage.example/file")


def test_reject_redirect():
    response = Response()
    response.status_code = 302
    with pytest.raises(probe.ProbeError, match="download_http_error"):
        probe.download(Session(response), "https://storage.example/file")


def test_stream_deadline(monkeypatch):
    moments = iter([0, 46])
    monkeypatch.setattr(probe.time, "monotonic", lambda: next(moments))
    with pytest.raises(probe.ProbeError, match="download_deadline"):
        probe.download(Session(Response()), "https://storage.example/file")


def test_offline_mode_never_imports_network_or_configuration(monkeypatch, tmp_path, capsys):
    import builtins
    import json
    from types import SimpleNamespace

    artifact = tmp_path / "recent.parquet"
    artifact.write_bytes(b"parquet")
    original_import = builtins.__import__
    fake_parquet = object()

    def guarded_import(name, *args, **kwargs):
        assert name != "requests" and not name.startswith("qagent")
        if name == "pyarrow.parquet":
            return SimpleNamespace(parquet=fake_parquet)
        return original_import(name, *args, **kwargs)

    def inspect(blob, parquet):
        assert blob == b"parquet"
        assert parquet is fake_parquet
        return {"read_only": True, "targets": []}

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(probe, "inspect_dump", inspect)
    assert probe.main(["--file", str(artifact)]) == 0
    assert json.loads(capsys.readouterr().out) == {"read_only": True, "targets": []}


def test_file_size_rejected_before_read(monkeypatch, tmp_path):
    artifact = tmp_path / "too-large.parquet"
    artifact.write_bytes(b"abcd")
    monkeypatch.setattr(probe, "MAX_BYTES", 3)
    original_open = Path.open

    class NoRead:
        def __enter__(self):
            self.file = original_open(artifact, "rb")
            return self
        def __exit__(self, *args):
            self.file.close()
        def fileno(self):
            return self.file.fileno()
        def read(self, *args):
            pytest.fail("oversized file must be rejected before reading")

    monkeypatch.setattr(Path, "open", lambda *args: NoRead())
    with pytest.raises(probe.ProbeError, match="file_size_limit"):
        probe.read_file(artifact)


def test_file_boundary_and_growth_guard(monkeypatch, tmp_path):
    from types import SimpleNamespace

    artifact = tmp_path / "recent.parquet"
    artifact.write_bytes(b"abc")
    monkeypatch.setattr(probe, "MAX_BYTES", 3)
    assert probe.read_file(artifact) == b"abc"
    artifact.write_bytes(b"abcd")
    monkeypatch.setattr(probe.os, "fstat", lambda _: SimpleNamespace(st_size=3))
    with pytest.raises(probe.ProbeError, match="file_size_limit"):
        probe.read_file(artifact)


@pytest.mark.parametrize("row_count,names,error", [
    (150_001, probe.COLUMNS, "unexpected_recent_dump_row_count"),
    (4, probe.COLUMNS[:-1], "unexpected_dump_schema"),
])
def test_reject_unexpected_parquet_before_batches(row_count, names, error):
    from types import SimpleNamespace

    fake_file = SimpleNamespace(metadata=SimpleNamespace(num_rows=row_count),
                                schema_arrow=SimpleNamespace(names=names))
    with pytest.raises(probe.ProbeError, match=error):
        probe.inspect_dump(b"fake", SimpleNamespace(ParquetFile=lambda _: fake_file))


def test_inspection_preserves_pair_dates_duplicates_and_raw_basis():
    import pandas as pd
    from types import SimpleNamespace
    records = [{"thscode": symbol, "date_ms": pd.Timestamp(day, tz="Asia/Shanghai").value // 10**6,
                "adjusted": "none", "interval": "1d", "currency": "CNY",
                "open_price": 10., "high_price": 12., "low_price": 9., "close_price": 11.}
               for symbol, day in probe.TARGETS[:2]]
    records.append(records[0].copy())
    frame = pd.DataFrame(records)
    fake_file = SimpleNamespace(metadata=SimpleNamespace(num_rows=3),
        schema_arrow=SimpleNamespace(names=probe.COLUMNS),
        iter_batches=lambda **kw: [SimpleNamespace(to_pandas=lambda: frame)])
    result = probe.inspect_dump(b"fake", SimpleNamespace(ParquetFile=lambda _: fake_file))
    assert [row["count"] for row in result["targets"]] == [2, 1, 0, 0]
    assert result["targets"][0]["duplicate_key"]
    assert result["price_basis"] == "unadjusted_only"
    frame["adjusted"] = "forward"
    with pytest.raises(probe.ProbeError, match="unexpected_dump_price_basis"):
        probe.inspect_dump(b"fake", SimpleNamespace(ParquetFile=lambda _: fake_file))

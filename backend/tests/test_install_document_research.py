import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "install_document_research", Path(__file__).resolve().parents[2] / "scripts/install_document_research.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


def original():
    template = (Path(__file__).resolve().parents[2] /
                "deploy/runit/qagent-tushare-research.cron.in").read_text()
    for key, value in {
        "SERVICE_USER": "luozhenkun", "ENV_FILE": installer.OLD_ENV,
        "PYTHON": "/opt/qagent/current/backend/.venv/bin/python", "APP_DIR": installer.OLD_BUNDLE,
        "RESEARCH_OUTPUT_DIR": "/var/lib/qagent-research/tushare-relay",
    }.items():
        template = template.replace("@" + key + "@", value)
    return template.encode()


def test_only_bundle_and_environment_change():
    old = original()
    new = installer.plan(old, installer.checksum(old))
    assert new.replace(installer.NEW_BUNDLE.encode(), installer.OLD_BUNDLE.encode()).replace(
        installer.NEW_ENV.encode(), installer.OLD_ENV.encode()) == old
    assert b"30 8 * * 1-5" in new
    assert b"--symbols CN:000001,CN:600519 --limit 2 --timeout 180" in new


@pytest.mark.parametrize("change", [
    lambda value: value.replace(b"30 8", b"0 8"),
    lambda value: value + b"* * * * * root true\n",
    lambda value: value.replace(b"--limit 2", b"--limit 3"),
    lambda value: value.replace(b"/var/lib/qagent-research/tushare-relay", b"/tmp/other"),
])
def test_unexpected_existing_cron_is_rejected(change):
    raw = change(original())
    with pytest.raises(ValueError):
        installer.plan(raw, installer.checksum(raw))


def test_digest_guard_rejects_changes():
    with pytest.raises(ValueError, match="cron_changed"):
        installer.plan(original(), "0" * 64)


def test_same_key_and_proxy_are_required_without_executing_environment():
    old = "QAGENT_TUSHARE_RELAY_KEY='synthetic-key'\n"
    installer.validate_environment(old, old + "HTTPS_PROXY='http://proxy.invalid:8080'\n")
    for new in (old, old.replace("synthetic-key", "other") + "HTTPS_PROXY='proxy'\n",
                old + "HTTPS_PROXY=$(malicious-command)\n",
                old + "HTTPS_PROXY=one\nHTTPS_PROXY=two\n"):
        with pytest.raises(ValueError):
            installer.validate_environment(old, new)


def test_symlink_files_rejected(tmp_path):
    target = tmp_path / "file"
    target.write_text("irrelevant")
    symlink = tmp_path / "link"
    symlink.symlink_to(target)
    with pytest.raises(ValueError, match="unsafe_path"):
        installer.regular(symlink)

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import upgrade_financial_global_control as upgrade
import run_daily_financial_v4 as wrapper
sys.path.pop(0)


def test_v4_only_changes_bundle_wiring_preserving_schedule_and_legacy_templates():
    old = upgrade.baseline.configured_engine()
    engine = upgrade.configured_engine()
    assert engine.old_daily_cron() == old.daily_cron()
    assert engine.old_forward_cron() == old.forward_cron()
    assert engine.DAILY_NEW_BUNDLE == wrapper.DAILY_BUNDLE
    assert engine.FORWARD_NEW_BUNDLE == wrapper.FORWARD_BUNDLE
    daily = engine.daily_cron().replace(str(engine.DAILY_NEW_BUNDLE).encode(),
                                       str(engine.DAILY_OLD_BUNDLE).encode())
    assert daily.replace(b"run_daily_financial_v4.py", b"run_daily_financial_same_day.py") == old.daily_cron()
    forward = engine.forward_cron().replace(str(engine.FORWARD_NEW_BUNDLE).encode(),
                                           str(engine.FORWARD_OLD_BUNDLE).encode())
    assert forward == old.forward_cron()
    assert all(len(line) < 1000 for line in engine.daily_cron().splitlines())

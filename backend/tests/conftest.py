import pytest

from qagent.market import instruments


_BASE_CN_INSTRUMENT_NAMES = instruments.CN_INSTRUMENT_NAMES.copy()


def _reset_cn_instrument_names() -> None:
    instruments.CN_INSTRUMENT_NAMES.clear()
    instruments.CN_INSTRUMENT_NAMES.update(_BASE_CN_INSTRUMENT_NAMES)
    instruments._CN_INSTRUMENT_NAMES_READY = True


@pytest.fixture(autouse=True)
def isolate_cn_instrument_name_registry():
    _reset_cn_instrument_names()
    yield
    _reset_cn_instrument_names()

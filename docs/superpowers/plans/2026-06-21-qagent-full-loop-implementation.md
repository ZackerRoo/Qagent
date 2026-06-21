# Qagent Full-Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a runnable US + China A-share stock opportunity system with daily scanning, key alert evaluation, opportunity cards, watchlist/position monitoring, outcomes, dashboard, and constrained agent answers.

**Architecture:** Use a Python FastAPI backend with SQLite persistence and deterministic domain engines for instruments, providers, signals, opportunity cards, alerts, and outcomes. Use a React + TypeScript dashboard that consumes backend APIs and shows dense operational views. LLM-style explanations are constrained to structured records first, with provider data caveats surfaced in every card.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, SQLAlchemy, SQLite, pandas, numpy, yfinance, akshare, baostock, exchange-calendars, pytest, ruff, Vite, React, TypeScript, lucide-react.

---

## File Structure

Create:

```text
backend/
  pyproject.toml
  README.md
  qagent/
    __init__.py
    app.py
    config.py
    db.py
    domain/
      __init__.py
      enums.py
      models.py
    providers/
      __init__.py
      base.py
      free_us.py
      free_cn.py
      fixtures.py
    market/
      __init__.py
      calendars.py
      indicators.py
      universe.py
    signals/
      __init__.py
      engine.py
      trend.py
      breakout.py
      pullback.py
      volume.py
      limit_status.py
    cards/
      __init__.py
      scoring.py
      entry_exit.py
      generator.py
    monitoring/
      __init__.py
      alerts.py
      portfolio.py
      outcomes.py
    agent/
      __init__.py
      responder.py
    api/
      __init__.py
      routes.py
      schemas.py
    jobs/
      __init__.py
      daily_scan.py
      intraday_check.py
    cli.py
  tests/
    conftest.py
    fixtures/
      us_daily_bars.csv
      cn_daily_bars.csv
    test_domain_models.py
    test_indicators.py
    test_signal_engine.py
    test_entry_exit.py
    test_alerts.py
    test_outcomes.py
    test_api_smoke.py

frontend/
  package.json
  index.html
  vite.config.ts
  tsconfig.json
  src/
    main.tsx
    App.tsx
    api/client.ts
    types.ts
    styles.css
    components/
      Layout.tsx
      StatusBadge.tsx
      DataHealth.tsx
      OpportunityTable.tsx
      OpportunityDetail.tsx
      AlertList.tsx
      PortfolioTable.tsx
      AgentPanel.tsx
    pages/
      Overview.tsx
      Opportunities.tsx
      Watchlist.tsx
      Portfolio.tsx
      Alerts.tsx
      Review.tsx
      Settings.tsx

data/
  .gitkeep

scripts/
  dev_backend.sh
  dev_frontend.sh
```

Modify:

```text
README.md
.gitignore
```

Do not create auto-trading or broker execution modules in this plan.

Every Python package directory listed above should include an `__init__.py` file in the same task that first creates that directory.

---

## Task 1: Repository Scaffold

**Files:**
- Create: `.gitignore`
- Create: `backend/pyproject.toml`
- Create: `backend/README.md`
- Create: `backend/qagent/__init__.py`
- Create: `backend/qagent/config.py`
- Create: `backend/qagent/app.py`
- Create: `backend/qagent/api/routes.py`
- Create: `backend/qagent/api/schemas.py`
- Create: `backend/tests/conftest.py`
- Create: `scripts/dev_backend.sh`
- Modify: `README.md`

- [ ] **Step 1: Write backend package configuration**

Create `backend/pyproject.toml`:

```toml
[project]
name = "qagent-backend"
version = "0.1.0"
description = "Qagent backend for US and China A-share opportunity scanning"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "pydantic>=2.8",
  "pydantic-settings>=2.4",
  "sqlalchemy>=2.0",
  "pandas>=2.2",
  "numpy>=2.0",
  "yfinance>=0.2",
  "akshare>=1.14",
  "baostock>=0.8",
  "exchange-calendars>=4.5",
  "python-dateutil>=2.9",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.3",
  "httpx>=0.27",
  "ruff>=0.6",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]

[tool.ruff]
line-length = 100
target-version = "py311"
```

- [ ] **Step 2: Create FastAPI smoke test**

Create `backend/tests/test_api_smoke.py`:

```python
from fastapi.testclient import TestClient

from qagent.app import create_app


def test_health_endpoint():
    client = TestClient(create_app())
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
```

- [ ] **Step 3: Run test to verify it fails**

Run:

```bash
cd backend
python -m pytest tests/test_api_smoke.py -v
```

Expected: FAIL because `qagent.app` does not exist.

- [ ] **Step 4: Implement app factory**

Create `backend/qagent/app.py`:

```python
from fastapi import FastAPI

from qagent.api.routes import router


def create_app() -> FastAPI:
    app = FastAPI(title="Qagent API", version="0.1.0")
    app.include_router(router, prefix="/api")
    return app


app = create_app()
```

Create `backend/qagent/api/routes.py`:

```python
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

Create empty package files:

```text
backend/qagent/__init__.py
backend/qagent/api/__init__.py
backend/qagent/api/schemas.py
```

- [ ] **Step 5: Add settings and docs**

Create `backend/qagent/config.py`:

```python
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///../data/qagent.db"
    data_dir: Path = Path("../data")
    environment: str = "development"

    model_config = SettingsConfigDict(env_prefix="QAGENT_", env_file=".env")


def get_settings() -> Settings:
    return Settings()
```

Create `backend/README.md`:

```markdown
# Qagent Backend

FastAPI backend for the Qagent research, alert, and review workflow.
```

Create `scripts/dev_backend.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../backend"
uvicorn qagent.app:app --reload --host 127.0.0.1 --port 8000
```

Create `.gitignore`:

```gitignore
.DS_Store
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
.venv/
node_modules/
dist/
data/*.db
data/*.db-*
.env
```

Update `README.md` with setup commands.

- [ ] **Step 6: Run test to verify it passes**

Run:

```bash
cd backend
python -m pytest tests/test_api_smoke.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add .gitignore README.md backend scripts/dev_backend.sh
git commit -m "feat: scaffold backend api"
```

---

## Task 2: Domain Models and Persistence

**Files:**
- Create: `backend/qagent/domain/enums.py`
- Create: `backend/qagent/domain/models.py`
- Create: `backend/qagent/db.py`
- Create: `backend/tests/test_domain_models.py`

- [ ] **Step 1: Write domain model tests**

Create `backend/tests/test_domain_models.py`:

```python
from qagent.domain.enums import Market, OpportunityStatus
from qagent.domain.models import Instrument


def test_instrument_id_for_us_stock():
    instrument = Instrument(
        market=Market.US,
        symbol="AAPL",
        exchange="NASDAQ",
        name="Apple Inc.",
        currency="USD",
        timezone="America/New_York",
        trading_calendar="XNYS",
    )
    assert instrument.instrument_id == "US:AAPL"


def test_instrument_id_for_cn_stock_keeps_leading_zeroes():
    instrument = Instrument(
        market=Market.CN,
        symbol="000001",
        exchange="SZSE",
        name="平安银行",
        currency="CNY",
        timezone="Asia/Shanghai",
        trading_calendar="XSHG_XSHE",
    )
    assert instrument.instrument_id == "CN:000001"


def test_opportunity_status_values_are_stable():
    assert OpportunityStatus.SETUP_READY.value == "setup_ready"
    assert OpportunityStatus.INVALIDATED.value == "invalidated"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend
python -m pytest tests/test_domain_models.py -v
```

Expected: FAIL because domain modules do not exist.

- [ ] **Step 3: Implement enums and Pydantic domain models**

Create `backend/qagent/domain/enums.py`:

```python
from enum import StrEnum


class Market(StrEnum):
    US = "US"
    CN = "CN"


class OpportunityStatus(StrEnum):
    NEW_IDEA = "new_idea"
    WATCH = "watch"
    SETUP_READY = "setup_ready"
    TRIGGERED = "triggered"
    EXTENDED = "extended"
    ACTIVE = "active"
    RISK_ELEVATED = "risk_elevated"
    INVALIDATED = "invalidated"
    CLOSED = "closed"
    POSTMORTEM_DONE = "postmortem_done"


class SignalType(StrEnum):
    TREND_STRENGTH = "trend_strength"
    PULLBACK = "pullback"
    BREAKOUT = "breakout"
    VOLUME_ANOMALY = "volume_anomaly"
    LIMIT_STATUS = "limit_status"
    EVENT_CATALYST = "event_catalyst"


class Direction(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class AlertStatus(StrEnum):
    PENDING = "pending"
    TRIGGERED = "triggered"
    ACKNOWLEDGED = "acknowledged"
    CLOSED = "closed"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"
```

Create `backend/qagent/domain/models.py`:

```python
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from qagent.domain.enums import Direction, Market, OpportunityStatus, SignalType


class Instrument(BaseModel):
    market: Market
    symbol: str
    exchange: str
    name: str
    currency: str
    timezone: str
    trading_calendar: str
    asset_type: str = "stock"

    @property
    def instrument_id(self) -> str:
        return f"{self.market.value}:{self.symbol}"


class DailyBar(BaseModel):
    instrument_id: str
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    provider: str


class Signal(BaseModel):
    instrument_id: str
    signal_type: SignalType
    direction: Direction
    observed_at: datetime
    horizon: str
    score: float = Field(ge=0.0, le=1.0)
    evidence: dict[str, object] = Field(default_factory=dict)
    provider: str = "computed"


class EntryPlan(BaseModel):
    entry_type: str
    trigger_price: Decimal | None = None
    entry_zone_low: Decimal | None = None
    entry_zone_high: Decimal | None = None
    confirmation: str
    no_chase_above: Decimal | None = None


class ExitPlan(BaseModel):
    initial_stop: Decimal | None = None
    invalidation: str
    target_1: Decimal | None = None
    target_2: Decimal | None = None
    trailing_rule: str
    time_stop: str


class OpportunityCard(BaseModel):
    card_id: str
    instrument_id: str
    market: Market
    status: OpportunityStatus
    thesis: str
    score: float = Field(ge=0.0, le=1.0)
    entry_plan: EntryPlan
    exit_plan: ExitPlan
    risk_reward: float | None = None
    data_caveats: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Add SQLAlchemy database bootstrap**

Create `backend/qagent/db.py`:

```python
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from qagent.config import get_settings


class Base(DeclarativeBase):
    pass


def create_db_engine(database_url: str | None = None):
    settings = get_settings()
    return create_engine(database_url or settings.database_url, future=True)


def create_session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=create_db_engine(database_url), expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    session_factory = create_session_factory()
    with session_factory() as session:
        yield session
```

- [ ] **Step 5: Run tests**

```bash
cd backend
python -m pytest tests/test_domain_models.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/qagent/domain backend/qagent/db.py backend/tests/test_domain_models.py
git commit -m "feat: add core domain models"
```

---

## Task 3: Market Calendars and Indicators

**Files:**
- Create: `backend/qagent/market/calendars.py`
- Create: `backend/qagent/market/indicators.py`
- Create: `backend/tests/test_indicators.py`

- [ ] **Step 1: Add indicator tests**

Create `backend/tests/test_indicators.py`:

```python
import pandas as pd

from qagent.market.indicators import add_moving_averages, add_volume_ratio, percent_distance


def test_add_moving_averages():
    frame = pd.DataFrame({"close": list(range(1, 61))})
    result = add_moving_averages(frame, windows=(20, 50))
    assert round(result["ma_20"].iloc[-1], 2) == 50.5
    assert round(result["ma_50"].iloc[-1], 2) == 35.5


def test_percent_distance():
    assert percent_distance(110, 100) == 10.0
    assert percent_distance(90, 100) == -10.0


def test_add_volume_ratio():
    frame = pd.DataFrame({"volume": [100] * 20 + [300]})
    result = add_volume_ratio(frame, window=20)
    assert result["volume_ratio"].iloc[-1] == 3.0
```

- [ ] **Step 2: Run tests to verify failure**

```bash
cd backend
python -m pytest tests/test_indicators.py -v
```

Expected: FAIL because market modules do not exist.

- [ ] **Step 3: Implement indicators**

Create `backend/qagent/market/indicators.py`:

```python
import pandas as pd


def add_moving_averages(frame: pd.DataFrame, windows: tuple[int, ...]) -> pd.DataFrame:
    result = frame.copy()
    for window in windows:
        result[f"ma_{window}"] = result["close"].rolling(window=window).mean()
    return result


def add_volume_ratio(frame: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    result = frame.copy()
    average_volume = result["volume"].rolling(window=window).mean().shift(1)
    result["volume_ratio"] = (result["volume"] / average_volume).round(4)
    return result


def percent_distance(value: float, reference: float) -> float:
    if reference == 0:
        raise ValueError("reference cannot be zero")
    return round((value - reference) / reference * 100, 4)
```

Create `backend/qagent/market/calendars.py`:

```python
from datetime import date

from qagent.domain.enums import Market


def market_timezone(market: Market) -> str:
    return "America/New_York" if market == Market.US else "Asia/Shanghai"


def trading_calendar_name(market: Market) -> str:
    return "XNYS" if market == Market.US else "XSHG_XSHE"


def trading_day_offset(anchor: date, days: int) -> date:
    # Initial implementation: weekday-only fallback.
    # Replace with exchange-calendars integration after core tests are stable.
    step = 1 if days >= 0 else -1
    remaining = abs(days)
    current = anchor
    from datetime import timedelta

    while remaining:
        current = current + timedelta(days=step)
        if current.weekday() < 5:
            remaining -= 1
    return current
```

- [ ] **Step 4: Run tests**

```bash
cd backend
python -m pytest tests/test_indicators.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/qagent/market backend/tests/test_indicators.py
git commit -m "feat: add market indicators"
```

---

## Task 4: Free Provider Interfaces and Fixture Providers

**Files:**
- Create: `backend/qagent/providers/base.py`
- Create: `backend/qagent/providers/free_us.py`
- Create: `backend/qagent/providers/free_cn.py`
- Create: `backend/qagent/providers/fixtures.py`
- Create: `backend/tests/fixtures/us_daily_bars.csv`
- Create: `backend/tests/fixtures/cn_daily_bars.csv`
- Create: `backend/tests/test_providers.py`

- [ ] **Step 1: Add provider tests**

Create `backend/tests/test_providers.py`:

```python
from datetime import date

from qagent.providers.fixtures import FixtureMarketDataProvider


def test_fixture_provider_loads_us_bars():
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(["US:TEST"], date(2026, 1, 1), date(2026, 3, 31))
    assert not bars.empty
    assert set(["instrument_id", "trade_date", "open", "high", "low", "close", "volume"]).issubset(
        bars.columns
    )


def test_fixture_provider_filters_instrument_ids():
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(["CN:000001"], date(2026, 1, 1), date(2026, 3, 31))
    assert bars["instrument_id"].eq("CN:000001").all()
```

- [ ] **Step 2: Create small deterministic fixture CSV files**

Create `backend/tests/fixtures/us_daily_bars.csv` with at least 80 rows for `US:TEST`.

Create `backend/tests/fixtures/cn_daily_bars.csv` with at least 80 rows for `CN:000001`.

Use synthetic but realistic OHLCV values. Ensure the last rows include one breakout-like move and one volume spike.

- [ ] **Step 3: Implement provider protocols and fixtures**

Create `backend/qagent/providers/base.py`:

```python
from datetime import date
from typing import Protocol

import pandas as pd


class MarketDataProvider(Protocol):
    name: str

    def get_daily_bars(
        self, instrument_ids: list[str], start: date, end: date
    ) -> pd.DataFrame:
        ...

    def get_snapshot(self, instrument_ids: list[str]) -> pd.DataFrame:
        ...
```

Create `backend/qagent/providers/fixtures.py`:

```python
from datetime import date
from pathlib import Path

import pandas as pd


class FixtureMarketDataProvider:
    name = "fixture"

    def __init__(self, fixture_dir: Path | None = None):
        self.fixture_dir = fixture_dir or Path(__file__).parents[2] / "tests" / "fixtures"

    def get_daily_bars(
        self, instrument_ids: list[str], start: date, end: date
    ) -> pd.DataFrame:
        frames = []
        for path in [self.fixture_dir / "us_daily_bars.csv", self.fixture_dir / "cn_daily_bars.csv"]:
            if path.exists():
                frames.append(pd.read_csv(path, parse_dates=["trade_date"]))
        frame = pd.concat(frames, ignore_index=True)
        frame["trade_date"] = frame["trade_date"].dt.date
        mask = (
            frame["instrument_id"].isin(instrument_ids)
            & (frame["trade_date"] >= start)
            & (frame["trade_date"] <= end)
        )
        return frame.loc[mask].sort_values(["instrument_id", "trade_date"]).reset_index(drop=True)

    def get_snapshot(self, instrument_ids: list[str]) -> pd.DataFrame:
        frame = self.get_daily_bars(instrument_ids, date(1900, 1, 1), date(2100, 1, 1))
        return frame.groupby("instrument_id").tail(1).reset_index(drop=True)
```

Create `backend/qagent/providers/free_us.py` and `backend/qagent/providers/free_cn.py` with thin placeholders that raise clear `NotImplementedError` until the fixture path is stable.

- [ ] **Step 4: Run provider tests**

```bash
cd backend
python -m pytest tests/test_providers.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/qagent/providers backend/tests/fixtures backend/tests/test_providers.py
git commit -m "feat: add market data provider interfaces"
```

---

## Task 5: Signal Engine

**Files:**
- Create: `backend/qagent/signals/engine.py`
- Create: `backend/qagent/signals/trend.py`
- Create: `backend/qagent/signals/breakout.py`
- Create: `backend/qagent/signals/pullback.py`
- Create: `backend/qagent/signals/volume.py`
- Create: `backend/qagent/signals/limit_status.py`
- Create: `backend/tests/test_signal_engine.py`

- [ ] **Step 1: Add signal engine tests**

Create `backend/tests/test_signal_engine.py`:

```python
from datetime import date

from qagent.providers.fixtures import FixtureMarketDataProvider
from qagent.signals.engine import SignalEngine


def test_signal_engine_generates_trend_and_volume_signals():
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(["US:TEST"], date(2026, 1, 1), date(2026, 3, 31))
    signals = SignalEngine().generate("US:TEST", bars)
    signal_types = {signal.signal_type.value for signal in signals}
    assert "trend_strength" in signal_types
    assert "volume_anomaly" in signal_types


def test_signal_engine_generates_cn_limit_signal_when_near_limit():
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(["CN:000001"], date(2026, 1, 1), date(2026, 3, 31))
    signals = SignalEngine().generate("CN:000001", bars)
    assert any(signal.signal_type.value == "limit_status" for signal in signals)
```

- [ ] **Step 2: Run tests to verify failure**

```bash
cd backend
python -m pytest tests/test_signal_engine.py -v
```

Expected: FAIL because signal modules do not exist.

- [ ] **Step 3: Implement signal detectors**

Implement:

- `trend.py`: price above 20/50/200 moving averages and 20MA above 50MA.
- `pullback.py`: price within configurable range of 20/50MA while longer trend remains up.
- `breakout.py`: close above prior 20-day high with volume confirmation.
- `volume.py`: latest volume ratio above 1.8.
- `limit_status.py`: CN near-limit or limit-up/down using latest close vs previous close.

Each detector returns `Signal` objects with evidence dicts.

- [ ] **Step 4: Implement engine orchestration**

Create `backend/qagent/signals/engine.py`:

```python
import pandas as pd

from qagent.domain.models import Signal
from qagent.signals.breakout import detect_breakout
from qagent.signals.limit_status import detect_limit_status
from qagent.signals.pullback import detect_pullback
from qagent.signals.trend import detect_trend_strength
from qagent.signals.volume import detect_volume_anomaly


class SignalEngine:
    def generate(self, instrument_id: str, bars: pd.DataFrame) -> list[Signal]:
        if bars.empty or len(bars) < 60:
            return []
        detectors = [
            detect_trend_strength,
            detect_pullback,
            detect_breakout,
            detect_volume_anomaly,
            detect_limit_status,
        ]
        signals: list[Signal] = []
        for detector in detectors:
            signal = detector(instrument_id, bars)
            if signal is not None:
                signals.append(signal)
        return signals
```

- [ ] **Step 5: Run tests**

```bash
cd backend
python -m pytest tests/test_signal_engine.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/qagent/signals backend/tests/test_signal_engine.py
git commit -m "feat: add core signal engine"
```

---

## Task 6: Entry/Exit and Opportunity Card Generation

**Files:**
- Create: `backend/qagent/cards/entry_exit.py`
- Create: `backend/qagent/cards/scoring.py`
- Create: `backend/qagent/cards/generator.py`
- Create: `backend/tests/test_entry_exit.py`
- Create: `backend/tests/test_card_generation.py`

- [ ] **Step 1: Add entry/exit tests**

Create `backend/tests/test_entry_exit.py`:

```python
from decimal import Decimal

from qagent.cards.entry_exit import build_breakout_plan


def test_breakout_plan_has_trigger_stop_target_and_no_chase():
    plan = build_breakout_plan(
        latest_close=Decimal("100"),
        pivot=Decimal("102"),
        atr=Decimal("4"),
    )
    assert plan.entry_plan.trigger_price == Decimal("102")
    assert plan.entry_plan.no_chase_above == Decimal("106")
    assert plan.exit_plan.initial_stop == Decimal("98")
    assert plan.exit_plan.target_1 == Decimal("110")
    assert plan.risk_reward >= 2
```

- [ ] **Step 2: Add card generation tests**

Create `backend/tests/test_card_generation.py`:

```python
from datetime import date

from qagent.cards.generator import OpportunityCardGenerator
from qagent.providers.fixtures import FixtureMarketDataProvider
from qagent.signals.engine import SignalEngine


def test_card_generator_creates_setup_ready_card():
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(["US:TEST"], date(2026, 1, 1), date(2026, 3, 31))
    signals = SignalEngine().generate("US:TEST", bars)
    card = OpportunityCardGenerator().generate("US:TEST", signals, bars)
    assert card is not None
    assert card.entry_plan.confirmation
    assert card.exit_plan.invalidation
    assert card.data_caveats == ["fixture data"]
```

- [ ] **Step 3: Run tests to verify failure**

```bash
cd backend
python -m pytest tests/test_entry_exit.py tests/test_card_generation.py -v
```

Expected: FAIL because card modules do not exist.

- [ ] **Step 4: Implement deterministic entry/exit functions**

Create `backend/qagent/cards/entry_exit.py` with:

- `TradePlan` Pydantic model containing `entry_plan`, `exit_plan`, and `risk_reward`.
- `build_breakout_plan(latest_close, pivot, atr)`.
- `build_pullback_plan(latest_close, support, atr)`.
- Price math using `Decimal`.

Rules:

- no-chase = trigger + 1 ATR for breakout;
- stop = trigger - 1 ATR;
- target 1 = trigger + 2 ATR;
- target 2 = trigger + 3 ATR;
- risk/reward = `(target_1 - trigger) / (trigger - stop)`.

- [ ] **Step 5: Implement scoring and card generator**

Create `backend/qagent/cards/scoring.py`:

```python
from qagent.domain.models import Signal


SIGNAL_WEIGHTS = {
    "trend_strength": 0.25,
    "pullback": 0.2,
    "breakout": 0.25,
    "volume_anomaly": 0.15,
    "limit_status": 0.15,
    "event_catalyst": 0.15,
}


def aggregate_score(signals: list[Signal]) -> float:
    if not signals:
        return 0.0
    score = 0.0
    weight_sum = 0.0
    for signal in signals:
        weight = SIGNAL_WEIGHTS.get(signal.signal_type.value, 0.1)
        score += signal.score * weight
        weight_sum += weight
    return round(score / weight_sum, 4) if weight_sum else 0.0
```

Create `backend/qagent/cards/generator.py`:

```python
from decimal import Decimal
from uuid import uuid4

import pandas as pd

from qagent.cards.entry_exit import build_breakout_plan
from qagent.cards.scoring import aggregate_score
from qagent.domain.enums import Market, OpportunityStatus
from qagent.domain.models import OpportunityCard, Signal


class OpportunityCardGenerator:
    def generate(
        self, instrument_id: str, signals: list[Signal], bars: pd.DataFrame
    ) -> OpportunityCard | None:
        if not signals or bars.empty:
            return None
        latest = bars.iloc[-1]
        close = Decimal(str(round(float(latest["close"]), 2)))
        atr = Decimal(str(round(max(float(close) * 0.04, 0.01), 2)))
        plan = build_breakout_plan(latest_close=close, pivot=close, atr=atr)
        score = aggregate_score(signals)
        market = Market.US if instrument_id.startswith("US:") else Market.CN
        return OpportunityCard(
            card_id=f"card_{uuid4().hex[:12]}",
            instrument_id=instrument_id,
            market=market,
            status=OpportunityStatus.SETUP_READY if score >= 0.5 else OpportunityStatus.WATCH,
            thesis="Signal stack indicates a watchable setup. Review data caveats before action.",
            score=score,
            entry_plan=plan.entry_plan,
            exit_plan=plan.exit_plan,
            risk_reward=plan.risk_reward,
            data_caveats=["fixture data"],
        )
```

- [ ] **Step 6: Run tests**

```bash
cd backend
python -m pytest tests/test_entry_exit.py tests/test_card_generation.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/qagent/cards backend/tests/test_entry_exit.py backend/tests/test_card_generation.py
git commit -m "feat: generate opportunity cards"
```

---

## Task 7: Alerts, Portfolio, and Outcomes

**Files:**
- Create: `backend/qagent/monitoring/alerts.py`
- Create: `backend/qagent/monitoring/portfolio.py`
- Create: `backend/qagent/monitoring/outcomes.py`
- Create: `backend/tests/test_alerts.py`
- Create: `backend/tests/test_outcomes.py`

- [ ] **Step 1: Add alert tests**

Create `backend/tests/test_alerts.py`:

```python
from decimal import Decimal

from qagent.monitoring.alerts import AlertRule, evaluate_price_alert


def test_entry_alert_triggers_when_price_crosses_above_level():
    rule = AlertRule(
        rule_id="r1",
        instrument_id="US:TEST",
        kind="entry_trigger",
        operator=">=",
        threshold=Decimal("100"),
    )
    alert = evaluate_price_alert(rule, Decimal("101"))
    assert alert is not None
    assert alert.status == "triggered"
```

- [ ] **Step 2: Add outcome tests**

Create `backend/tests/test_outcomes.py`:

```python
from datetime import date

from qagent.monitoring.outcomes import compute_forward_returns
from qagent.providers.fixtures import FixtureMarketDataProvider


def test_compute_forward_returns():
    provider = FixtureMarketDataProvider()
    bars = provider.get_daily_bars(["US:TEST"], date(2026, 1, 1), date(2026, 3, 31))
    result = compute_forward_returns(bars, signal_date=bars["trade_date"].iloc[20], horizons=(1, 5, 10))
    assert set(result.keys()) == {"return_1d", "return_5d", "return_10d"}
```

- [ ] **Step 3: Run tests to verify failure**

```bash
cd backend
python -m pytest tests/test_alerts.py tests/test_outcomes.py -v
```

Expected: FAIL because modules do not exist.

- [ ] **Step 4: Implement alert evaluation**

Create `backend/qagent/monitoring/alerts.py`:

```python
from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel


class AlertRule(BaseModel):
    rule_id: str
    instrument_id: str
    kind: str
    operator: str
    threshold: Decimal


class Alert(BaseModel):
    rule_id: str
    instrument_id: str
    kind: str
    status: str
    triggered_at: datetime
    message: str


def evaluate_price_alert(rule: AlertRule, latest_price: Decimal) -> Alert | None:
    triggered = (
        latest_price >= rule.threshold if rule.operator == ">=" else latest_price <= rule.threshold
    )
    if not triggered:
        return None
    return Alert(
        rule_id=rule.rule_id,
        instrument_id=rule.instrument_id,
        kind=rule.kind,
        status="triggered",
        triggered_at=datetime.now(timezone.utc),
        message=f"{rule.kind} triggered at {latest_price}",
    )
```

- [ ] **Step 5: Implement outcomes**

Create `backend/qagent/monitoring/outcomes.py`:

```python
from datetime import date

import pandas as pd


def compute_forward_returns(
    bars: pd.DataFrame, signal_date: date, horizons: tuple[int, ...] = (1, 5, 10, 20, 60)
) -> dict[str, float | None]:
    ordered = bars.sort_values("trade_date").reset_index(drop=True)
    matches = ordered.index[ordered["trade_date"] == signal_date].tolist()
    if not matches:
        raise ValueError("signal_date not found in bars")
    base_index = matches[0]
    base_close = float(ordered.loc[base_index, "close"])
    result: dict[str, float | None] = {}
    for horizon in horizons:
        target_index = base_index + horizon
        key = f"return_{horizon}d"
        if target_index >= len(ordered):
            result[key] = None
        else:
            future_close = float(ordered.loc[target_index, "close"])
            result[key] = round((future_close / base_close - 1) * 100, 4)
    return result
```

Create `backend/qagent/monitoring/portfolio.py` with a Pydantic `PositionInput` model and pure functions for unrealized return.

- [ ] **Step 6: Run tests**

```bash
cd backend
python -m pytest tests/test_alerts.py tests/test_outcomes.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/qagent/monitoring backend/tests/test_alerts.py backend/tests/test_outcomes.py
git commit -m "feat: add alerts and outcomes"
```

---

## Task 8: Daily Scan and Intraday Check Jobs

**Files:**
- Create: `backend/qagent/jobs/daily_scan.py`
- Create: `backend/qagent/jobs/intraday_check.py`
- Create: `backend/qagent/market/universe.py`
- Create: `backend/qagent/cli.py`
- Create: `backend/tests/test_jobs.py`

- [ ] **Step 1: Add job tests**

Create `backend/tests/test_jobs.py`:

```python
from qagent.jobs.daily_scan import run_daily_scan
from qagent.providers.fixtures import FixtureMarketDataProvider


def test_daily_scan_returns_cards_for_fixture_universe():
    result = run_daily_scan(
        instrument_ids=["US:TEST", "CN:000001"],
        provider=FixtureMarketDataProvider(),
    )
    assert len(result.cards) >= 1
    assert result.data_health["provider"] == "fixture"
```

- [ ] **Step 2: Implement universe and job result models**

Create `backend/qagent/market/universe.py` with a default fixture universe:

```python
DEFAULT_DEV_UNIVERSE = ["US:TEST", "CN:000001"]
```

Create `backend/qagent/jobs/daily_scan.py`:

```python
from datetime import date

from pydantic import BaseModel

from qagent.cards.generator import OpportunityCardGenerator
from qagent.domain.models import OpportunityCard
from qagent.providers.base import MarketDataProvider
from qagent.signals.engine import SignalEngine


class DailyScanResult(BaseModel):
    cards: list[OpportunityCard]
    data_health: dict[str, str]


def run_daily_scan(instrument_ids: list[str], provider: MarketDataProvider) -> DailyScanResult:
    cards = []
    signal_engine = SignalEngine()
    card_generator = OpportunityCardGenerator()
    for instrument_id in instrument_ids:
        bars = provider.get_daily_bars(instrument_ids=[instrument_id], start=date(2026, 1, 1), end=date(2026, 12, 31))
        signals = signal_engine.generate(instrument_id, bars)
        card = card_generator.generate(instrument_id, signals, bars)
        if card:
            cards.append(card)
    return DailyScanResult(cards=cards, data_health={"provider": provider.name, "mode": "development"})
```

Create `backend/qagent/jobs/intraday_check.py` with a function that evaluates latest snapshots against alert rules.

- [ ] **Step 3: Add CLI**

Create `backend/qagent/cli.py`:

```python
from qagent.jobs.daily_scan import run_daily_scan
from qagent.market.universe import DEFAULT_DEV_UNIVERSE
from qagent.providers.fixtures import FixtureMarketDataProvider


def main() -> None:
    result = run_daily_scan(DEFAULT_DEV_UNIVERSE, FixtureMarketDataProvider())
    for card in result.cards:
        print(f"{card.instrument_id} {card.status.value} score={card.score}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests and CLI**

```bash
cd backend
python -m pytest tests/test_jobs.py -v
python -m qagent.cli
```

Expected: tests PASS and CLI prints at least one card.

- [ ] **Step 5: Commit**

```bash
git add backend/qagent/jobs backend/qagent/market/universe.py backend/qagent/cli.py backend/tests/test_jobs.py
git commit -m "feat: add daily scan job"
```

---

## Task 9: Backend API Endpoints

**Files:**
- Modify: `backend/qagent/api/routes.py`
- Create: `backend/qagent/api/schemas.py`
- Create: `backend/tests/test_api_opportunities.py`

- [ ] **Step 1: Add API tests**

Create `backend/tests/test_api_opportunities.py`:

```python
from fastapi.testclient import TestClient

from qagent.app import create_app


def test_opportunities_endpoint_returns_cards():
    client = TestClient(create_app())
    response = client.get("/api/opportunities")
    assert response.status_code == 200
    body = response.json()
    assert "cards" in body
    assert "data_health" in body


def test_agent_endpoint_answers_from_card_context():
    client = TestClient(create_app())
    response = client.post("/api/agent/query", json={"question": "Why is US:TEST on the list?"})
    assert response.status_code == 200
    assert response.json()["answer"]
```

- [ ] **Step 2: Implement API schemas and routes**

Create API request/response models in `backend/qagent/api/schemas.py`.

Modify `backend/qagent/api/routes.py` to expose:

- `GET /api/health`
- `GET /api/overview`
- `GET /api/opportunities`
- `GET /api/alerts`
- `GET /api/portfolio`
- `POST /api/agent/query`

Use fixture provider and in-memory job results for the first pass. Do not add auth yet.

- [ ] **Step 3: Run API tests**

```bash
cd backend
python -m pytest tests/test_api_smoke.py tests/test_api_opportunities.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/qagent/api backend/tests/test_api_opportunities.py
git commit -m "feat: expose opportunity api"
```

---

## Task 10: Constrained Agent Responder

**Files:**
- Create: `backend/qagent/agent/responder.py`
- Create: `backend/tests/test_agent_responder.py`
- Modify: `backend/qagent/api/routes.py`

- [ ] **Step 1: Add responder tests**

Create `backend/tests/test_agent_responder.py`:

```python
from qagent.agent.responder import answer_question


def test_responder_refuses_to_guarantee_returns():
    answer = answer_question("Will US:TEST definitely go up?", context={})
    assert "cannot guarantee" in answer.lower()


def test_responder_answers_from_context():
    answer = answer_question(
        "Where is the stop?",
        context={"instrument_id": "US:TEST", "initial_stop": "98.00", "status": "setup_ready"},
    )
    assert "98.00" in answer
```

- [ ] **Step 2: Implement deterministic responder**

Create `backend/qagent/agent/responder.py`:

```python
RISK_TERMS = ["definitely", "guarantee", "sure win", "稳赚", "必涨"]


def answer_question(question: str, context: dict[str, object]) -> str:
    lowered = question.lower()
    if any(term in lowered for term in RISK_TERMS):
        return "I cannot guarantee returns. I can explain the setup, trigger, invalidation, and risks."
    if "stop" in lowered or "止损" in question:
        stop = context.get("initial_stop")
        if stop:
            return f"The current initial stop is {stop}. Treat it as an invalidation/risk level, not advice."
    instrument_id = context.get("instrument_id", "this instrument")
    status = context.get("status", "unknown")
    return f"{instrument_id} is currently in status {status}. Review the card's trigger, stop, targets, and data caveats."
```

- [ ] **Step 3: Wire endpoint to responder**

Modify `POST /api/agent/query` to call `answer_question`.

- [ ] **Step 4: Run tests**

```bash
cd backend
python -m pytest tests/test_agent_responder.py tests/test_api_opportunities.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/qagent/agent backend/qagent/api/routes.py backend/tests/test_agent_responder.py
git commit -m "feat: add constrained agent responder"
```

---

## Task 11: Frontend Scaffold

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/index.html`
- Create: `frontend/vite.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/styles.css`
- Create: `frontend/src/types.ts`
- Create: `frontend/src/api/client.ts`
- Create: `scripts/dev_frontend.sh`

- [ ] **Step 1: Create Vite React scaffold manually**

Create `frontend/package.json`:

```json
{
  "scripts": {
    "dev": "vite --host 127.0.0.1 --port 5173",
    "build": "tsc && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "@vitejs/plugin-react": "latest",
    "vite": "latest",
    "typescript": "latest",
    "react": "latest",
    "react-dom": "latest",
    "lucide-react": "latest"
  },
  "devDependencies": {}
}
```

Create `frontend/src/main.tsx`, `frontend/src/App.tsx`, and `frontend/src/styles.css` with a static dashboard shell.

- [ ] **Step 2: Add API client**

Create `frontend/src/api/client.ts`:

```typescript
const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000/api";

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) {
    throw new Error(`API request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}
```

- [ ] **Step 3: Add frontend dev script**

Create `scripts/dev_frontend.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../frontend"
npm install
npm run dev
```

- [ ] **Step 4: Build frontend**

Run:

```bash
cd frontend
npm install
npm run build
```

Expected: build succeeds.

- [ ] **Step 5: Commit**

```bash
git add frontend scripts/dev_frontend.sh
git commit -m "feat: scaffold frontend dashboard"
```

---

## Task 12: Dashboard Pages and Components

**Files:**
- Create: `frontend/src/components/Layout.tsx`
- Create: `frontend/src/components/StatusBadge.tsx`
- Create: `frontend/src/components/DataHealth.tsx`
- Create: `frontend/src/components/OpportunityTable.tsx`
- Create: `frontend/src/components/OpportunityDetail.tsx`
- Create: `frontend/src/components/AlertList.tsx`
- Create: `frontend/src/components/PortfolioTable.tsx`
- Create: `frontend/src/components/AgentPanel.tsx`
- Create: `frontend/src/pages/Overview.tsx`
- Create: `frontend/src/pages/Opportunities.tsx`
- Create: `frontend/src/pages/Watchlist.tsx`
- Create: `frontend/src/pages/Portfolio.tsx`
- Create: `frontend/src/pages/Alerts.tsx`
- Create: `frontend/src/pages/Review.tsx`
- Create: `frontend/src/pages/Settings.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Build layout**

Implement a dashboard layout:

- left nav;
- top market/session/data-health bar;
- main content;
- right agent panel.

Use dense table-first UI. Do not build a landing page.

- [ ] **Step 2: Build opportunity table**

`OpportunityTable` props:

```typescript
type Props = {
  cards: OpportunityCard[];
  selectedCardId?: string;
  onSelect(card: OpportunityCard): void;
};
```

Columns:

- ticker;
- market;
- status;
- score;
- entry trigger;
- stop;
- target 1;
- risk/reward;
- caveats.

- [ ] **Step 3: Build opportunity detail**

Show:

- thesis;
- signal stack summary;
- entry plan;
- exit plan;
- risk/reward;
- monitoring plan;
- data caveats.

- [ ] **Step 4: Build alerts and portfolio panels**

Render placeholder data from backend endpoints first. Keep components ready for real persistence.

- [ ] **Step 5: Build and verify**

Run:

```bash
cd frontend
npm run build
```

Expected: build succeeds.

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat: build dashboard workspace"
```

---

## Task 13: End-to-End Smoke Run

**Files:**
- Modify: `README.md`
- Create: `docs/development.md`

- [ ] **Step 1: Add development instructions**

Create `docs/development.md` with:

- backend setup;
- frontend setup;
- how to run tests;
- how to start both servers;
- known free-data limitations.

- [ ] **Step 2: Run backend tests**

```bash
cd backend
python -m pytest -v
```

Expected: all tests PASS.

- [ ] **Step 3: Run frontend build**

```bash
cd frontend
npm run build
```

Expected: build succeeds.

- [ ] **Step 4: Start backend**

```bash
cd backend
uvicorn qagent.app:app --host 127.0.0.1 --port 8000
```

Expected: backend starts and `/api/health` returns `{"status":"ok"}`.

- [ ] **Step 5: Start frontend**

```bash
cd frontend
npm run dev
```

Expected: frontend starts at `http://127.0.0.1:5173`.

- [ ] **Step 6: Manual dashboard verification**

Open `http://127.0.0.1:5173` and verify:

- overview loads;
- opportunity table shows fixture US and/or CN cards;
- selecting a card shows entry/exit plan;
- agent panel returns constrained answer;
- alerts page renders.

- [ ] **Step 7: Commit**

```bash
git add README.md docs/development.md
git commit -m "docs: add development workflow"
```

---

## Task 14: Real Free Provider Integration

**Files:**
- Modify: `backend/qagent/providers/free_us.py`
- Modify: `backend/qagent/providers/free_cn.py`
- Create: `backend/tests/test_free_provider_contracts.py`

- [ ] **Step 1: Add provider contract tests with monkeypatches**

Do not make tests depend on live network. Mock `yfinance`, `akshare`, and `baostock` return values.

Contract expectations:

- output columns match fixture provider;
- instrument IDs are normalized;
- provider caveats are exposed;
- provider failures raise a clear typed error or return an empty frame with a caveat.

- [ ] **Step 2: Implement US provider**

`free_us.py`:

- use `yfinance.download` for daily bars;
- normalize columns;
- support one or more US instrument IDs;
- return `provider="yfinance"`;
- include data caveat `"free data may be delayed"`.

- [ ] **Step 3: Implement CN provider**

`free_cn.py`:

- use `akshare` daily bar endpoints first;
- fallback to `baostock` if needed;
- normalize code format;
- preserve leading zeros;
- return `provider="akshare"` or `provider="baostock"`;
- include data caveat `"public CN data source may be incomplete"`.

- [ ] **Step 4: Run contract tests**

```bash
cd backend
python -m pytest tests/test_free_provider_contracts.py -v
```

Expected: PASS without network access.

- [ ] **Step 5: Commit**

```bash
git add backend/qagent/providers backend/tests/test_free_provider_contracts.py
git commit -m "feat: add free market data providers"
```

---

## Task 15: Final Verification and Push

**Files:**
- Modify only if verification reveals issues.

- [ ] **Step 1: Run all backend tests**

```bash
cd backend
python -m pytest -v
```

Expected: PASS.

- [ ] **Step 2: Run backend lint**

```bash
cd backend
python -m ruff check .
```

Expected: PASS.

- [ ] **Step 3: Run frontend build**

```bash
cd frontend
npm run build
```

Expected: PASS.

- [ ] **Step 4: Check git status**

```bash
git status -sb
```

Expected: clean except for intended changes before each commit.

- [ ] **Step 5: Push**

```bash
git push
```

Expected: remote `main` updates successfully.

---

## Implementation Notes

- Keep fixture provider as the deterministic test backbone even after real providers exist.
- Do not block core tests on live APIs.
- Do not add a broker module.
- Do not generate card prices with LLM text.
- Every card must show data caveats.
- Every alert must link back to a rule and object.
- A-share logic must preserve leading zeroes and respect涨跌停 behavior.
- Commit after each task or after each stable vertical slice.

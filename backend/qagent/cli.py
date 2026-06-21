from qagent.jobs.daily_scan import run_daily_scan
from qagent.market.universe import DEFAULT_DEV_UNIVERSE
from qagent.providers.fixtures import FixtureMarketDataProvider


def main() -> None:
    result = run_daily_scan(DEFAULT_DEV_UNIVERSE, FixtureMarketDataProvider())
    for card in result.cards:
        print(f"{card.instrument_id} {card.status.value} score={card.score}")


if __name__ == "__main__":
    main()

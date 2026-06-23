import type { Market } from "../types";

export type MarketSection = {
  market: Market;
  labelKey: "market.us" | "market.cn";
};

export type MarketSectionResult<T> = MarketSection & {
  items: T[];
};

export const MARKET_SECTIONS: MarketSection[] = [
  { market: "US", labelKey: "market.us" },
  { market: "CN", labelKey: "market.cn" },
];

export function getMarketFromInstrument(instrumentId: string): Market | null {
  const normalized = instrumentId.trim().toUpperCase();
  if (normalized.startsWith("US:")) {
    return "US";
  }
  if (normalized.startsWith("CN:")) {
    return "CN";
  }
  return null;
}

export function createMarketSections<T>(
  items: T[],
  getInstrumentId: (item: T) => string,
): MarketSectionResult<T>[] {
  return MARKET_SECTIONS.map((section) => ({
    ...section,
    items: items.filter((item) => getMarketFromInstrument(getInstrumentId(item)) === section.market),
  }));
}

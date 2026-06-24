const CN_INSTRUMENT_NAMES: Record<string, string> = {
  "000001": "平安银行",
  "000063": "中兴通讯",
  "002230": "科大讯飞",
  "002415": "海康威视",
  "002475": "立讯精密",
  "300124": "汇川技术",
  "300750": "宁德时代",
  "300760": "迈瑞医疗",
  "600036": "招商银行",
  "600519": "贵州茅台",
  "601318": "中国平安",
  "601398": "工商银行",
};

const US_INSTRUMENT_NAMES: Record<string, string> = {
  AAPL: "Apple",
  MSFT: "Microsoft",
  NVDA: "NVIDIA",
  TEST: "样例测试",
};

export function formatInstrumentLabel(instrumentId: string | null | undefined): string {
  const symbol = marketSymbol(instrumentId);
  if (!symbol) {
    return "-";
  }
  const market = marketPrefix(instrumentId);
  if (market === "CN") {
    const exchangeSymbol = `${symbol}.${cnExchangeSuffix(symbol)}`;
    const name = CN_INSTRUMENT_NAMES[symbol];
    return name ? `${name} ${exchangeSymbol}` : exchangeSymbol;
  }
  if (market === "US") {
    const name = US_INSTRUMENT_NAMES[symbol];
    return name ? `${name} ${symbol}` : symbol;
  }
  return symbol;
}

export function marketSymbol(instrumentId: string | null | undefined): string {
  if (!instrumentId) {
    return "";
  }
  const normalized = instrumentId.trim().toUpperCase();
  return normalized.includes(":") ? normalized.split(":", 2)[1] : normalized;
}

function marketPrefix(instrumentId: string | null | undefined): string {
  if (!instrumentId) {
    return "";
  }
  const normalized = instrumentId.trim().toUpperCase();
  return normalized.includes(":") ? normalized.split(":", 2)[0] : "";
}

function cnExchangeSuffix(symbol: string): string {
  if (symbol.startsWith("4") || symbol.startsWith("8")) {
    return "BJ";
  }
  if (symbol.startsWith("6")) {
    return "SH";
  }
  return "SZ";
}

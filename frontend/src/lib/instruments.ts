const CN_INSTRUMENT_NAMES: Record<string, string> = {
  "000001": "平安银行",
  "000063": "中兴通讯",
  "000333": "美的集团",
  "000651": "格力电器",
  "000725": "京东方A",
  "000858": "五粮液",
  "002230": "科大讯飞",
  "002241": "歌尔股份",
  "002415": "海康威视",
  "002475": "立讯精密",
  "002594": "比亚迪",
  "300033": "同花顺",
  "300059": "东方财富",
  "300124": "汇川技术",
  "300274": "阳光电源",
  "300308": "中际旭创",
  "300750": "宁德时代",
  "300760": "迈瑞医疗",
  "600030": "中信证券",
  "600036": "招商银行",
  "600276": "恒瑞医药",
  "600309": "万华化学",
  "600519": "贵州茅台",
  "600570": "恒生电子",
  "600690": "海尔智家",
  "600887": "伊利股份",
  "601012": "隆基绿能",
  "601166": "兴业银行",
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
  if (market === "CN" && symbol === "ALL") {
    return "全A股候选池";
  }
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

export function formatInstrumentDisplay(
  instrumentId: string | null | undefined,
  instrumentLabel?: string | null,
): string {
  return instrumentLabel?.trim() || formatInstrumentLabel(instrumentId);
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

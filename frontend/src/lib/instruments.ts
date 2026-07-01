let CN_INSTRUMENT_NAMES: Record<string, string> = {
  "000001": "平安银行",
  "000021": "深科技",
  "000026": "飞亚达",
  "000030": "富奥股份",
  "000063": "中兴通讯",
  "000333": "美的集团",
  "000651": "格力电器",
  "000725": "京东方A",
  "000858": "五粮液",
  "001309": "德明利",
  "002156": "通富微电",
  "002230": "科大讯飞",
  "002241": "歌尔股份",
  "002281": "光迅科技",
  "002371": "北方华创",
  "002415": "海康威视",
  "002475": "立讯精密",
  "002594": "比亚迪",
  "300033": "同花顺",
  "300059": "东方财富",
  "300124": "汇川技术",
  "300223": "北京君正",
  "300274": "阳光电源",
  "300308": "中际旭创",
  "300394": "天孚通信",
  "300475": "香农芯创",
  "300502": "新易盛",
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
  "603019": "中科曙光",
  "603986": "兆易创新",
  "601012": "隆基绿能",
  "601166": "兴业银行",
  "601318": "中国平安",
  "601398": "工商银行",
  "688008": "澜起科技",
  "688012": "中微公司",
  "688041": "海光信息",
  "688059": "华锐精密",
  "688111": "金山办公",
  "688126": "沪硅产业",
  "688256": "寒武纪",
  "688347": "华虹宏力",
  "688525": "佰维存储",
  "688981": "中芯国际",
  "301308": "江波龙",
  "510300": "沪深300ETF",
  "510500": "中证500ETF",
  "512100": "中证1000ETF",
  "588000": "科创50ETF",
  "159949": "创业板50ETF",
};

const CN_TOKEN_LABELS: Record<string, string> = {
  ALL: "全A股候选池",
  "INDEX:KCB50": "科创50成分股",
  "INDEX:CSI300": "沪深300成分股",
  "INDEX:CSI500": "中证500成分股",
  "INDEX:CSI1000": "中证1000成分股",
  "INDEX:CHINEXT50": "创业板50成分股",
  "ETF:KCB50": "科创50ETF",
  "ETF:CSI300": "沪深300ETF",
  "ETF:CSI500": "中证500ETF",
  "ETF:CSI1000": "中证1000ETF",
  "ETF:CHINEXT50": "创业板50ETF",
  "THEME:SEMICONDUCTOR": "半导体芯片主题",
  "THEME:MEMORY": "存储芯片主题",
  "THEME:AI_COMPUTE": "AI算力供应链主题",
};

const US_INSTRUMENT_NAMES: Record<string, string> = {
  AAPL: "Apple",
  MSFT: "Microsoft",
  NVDA: "NVIDIA",
  TEST: "开发标的",
};

export function registerInstrumentLabels(labels: Record<string, string>): number {
  let updated = 0;
  const nextMap = { ...CN_INSTRUMENT_NAMES };
  for (const [rawSymbol, rawName] of Object.entries(labels)) {
    const symbol = marketSymbol(rawSymbol);
    const normalizedName = formatLabelName(rawName);
    if (!symbol || !normalizedName) {
      continue;
    }
    const existing = nextMap[symbol];
    if (existing && existing.trim() === normalizedName) {
      continue;
    }
    nextMap[symbol] = normalizedName;
    updated += 1;
  }
  CN_INSTRUMENT_NAMES = nextMap;
  return updated;
}

function formatLabelName(rawName: string): string {
  const name = rawName.trim();
  if (!name) {
    return "";
  }

  const tokens = name.split(/\s+/);
  const filtered = [...tokens];

  while (filtered.length > 0) {
    const tail = filtered[filtered.length - 1];
    const upper = tail.toUpperCase();
    const looksLikeExchange = upper === "SH" || upper === "SZ" || upper === "BJ";
    const looksLikeCode = /^\d{6}$/.test(tail);
    const looksLikeCodeWithExchange = /^\d{6}\.(SH|SZ|BJ)$/i.test(tail);

    if (!looksLikeExchange && !looksLikeCode && !looksLikeCodeWithExchange) {
      break;
    }
    filtered.pop();
  }

  const normalized = filtered.join(" ");
  return normalized || name;
}

export function formatInstrumentLabel(instrumentId: string | null | undefined): string {
  const symbol = marketSymbol(instrumentId);
  if (!symbol) {
    return "-";
  }
  const market = marketPrefix(instrumentId);
  if (market === "CN" && CN_TOKEN_LABELS[symbol]) {
    return CN_TOKEN_LABELS[symbol];
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
  const fallback = formatInstrumentLabel(instrumentId);
  const trimmedLabel = instrumentLabel?.trim();
  if (!trimmedLabel) {
    return fallback;
  }
  if (marketPrefix(instrumentId) === "CN" && isBareCnCodeLabel(instrumentId, trimmedLabel)) {
    return fallback;
  }
  return trimmedLabel;
}

export function formatInstrumentText(
  value: string,
  instrumentId: string | null | undefined,
  instrumentLabel?: string | null,
): string {
  if (marketPrefix(instrumentId) !== "CN") {
    return value;
  }
  const symbol = marketSymbol(instrumentId);
  if (!symbol || !/^\d{6}$/.test(symbol)) {
    return value;
  }
  const display = formatInstrumentDisplay(instrumentId, instrumentLabel);
  if (!value || value.includes(display)) {
    return value;
  }
  const suffix = cnExchangeSuffix(symbol);
  const tokens = [`CN:${symbol}`, `${symbol}.${suffix}`, symbol];
  return tokens.reduce((text, token) => {
    const pattern = new RegExp(`(^|[\\s（(])${escapeRegExp(token)}(?=[:：,，\\s）)])`, "g");
    return text.replace(pattern, `$1${display}`);
  }, value);
}

export function marketSymbol(instrumentId: string | null | undefined): string {
  if (!instrumentId) {
    return "";
  }
  const normalized = instrumentId.trim().toUpperCase();
  const separator = normalized.indexOf(":");
  const symbol = separator >= 0 ? normalized.slice(separator + 1) : normalized;
  return stripExchangeSuffix(symbol);
}

export function hasInstrumentLabel(instrumentId: string | null | undefined): boolean {
  const symbol = marketSymbol(instrumentId);
  return !!symbol && isPlainCnCode(symbol) && Boolean(CN_INSTRUMENT_NAMES[symbol]);
}

function marketPrefix(instrumentId: string | null | undefined): string {
  if (!instrumentId) {
    return "";
  }
  const normalized = instrumentId.trim().toUpperCase();
  const separator = normalized.indexOf(":");
  if (separator >= 0) {
    return normalized.slice(0, separator);
  }
  return isPlainCnCode(normalized) ? "CN" : "";
}

function cnExchangeSuffix(symbol: string): string {
  if (symbol.startsWith("4") || symbol.startsWith("8") || symbol.startsWith("920")) {
    return "BJ";
  }
  if (symbol.startsWith("5") || symbol.startsWith("6")) {
    return "SH";
  }
  return "SZ";
}

function isPlainCnCode(symbol: string): boolean {
  return /^\d{6}$/.test(stripExchangeSuffix(symbol));
}

function stripExchangeSuffix(symbol: string): string {
  const normalized = symbol.trim().toUpperCase();
  const separator = normalized.indexOf(".");
  if (separator < 0) {
    return normalized;
  }
  const prefix = normalized.slice(0, separator);
  const suffix = normalized.slice(separator + 1);
  return suffix.match(/^(SH|SZ|BJ)$/) ? prefix : normalized;
}

function isBareCnCodeLabel(instrumentId: string | null | undefined, label: string): boolean {
  const symbol = marketSymbol(instrumentId);
  if (!symbol || !/^\d{6}$/.test(symbol)) {
    return false;
  }
  const normalizedLabel = label.trim().toUpperCase();
  const exchange = cnExchangeSuffix(symbol);
  return (
    normalizedLabel === symbol ||
    normalizedLabel === `CN:${symbol}` ||
    normalizedLabel === `${symbol}.${exchange}` ||
    normalizedLabel === `CN:${symbol}.${exchange}`
  );
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

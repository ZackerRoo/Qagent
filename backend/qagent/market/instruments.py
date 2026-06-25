CN_INSTRUMENT_NAMES = {
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
    "510300": "沪深300ETF",
    "510500": "中证500ETF",
    "512100": "中证1000ETF",
    "588000": "科创50ETF",
    "159949": "创业板50ETF",
}

US_INSTRUMENT_NAMES = {
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "NVDA": "NVIDIA",
    "TEST": "样例测试",
}

CN_TOKEN_LABELS = {
    "ALL": "全A股候选池",
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
}


def register_cn_instrument_names(names: dict[str, str]) -> None:
    for symbol, name in names.items():
        normalized_symbol = symbol.strip().upper()
        normalized_name = name.strip()
        if len(normalized_symbol) == 6 and normalized_symbol.isdigit() and normalized_name:
            CN_INSTRUMENT_NAMES[normalized_symbol] = normalized_name


def format_instrument_label(instrument_id: str | None) -> str:
    symbol = market_symbol(instrument_id)
    if not symbol:
        return "-"
    market = _market(instrument_id)
    if market == "CN" and symbol in CN_TOKEN_LABELS:
        return CN_TOKEN_LABELS[symbol]
    if market == "CN":
        name = CN_INSTRUMENT_NAMES.get(symbol)
        exchange_symbol = f"{symbol}.{_cn_exchange_suffix(symbol)}"
        return f"{name} {exchange_symbol}" if name else exchange_symbol
    if market == "US":
        name = US_INSTRUMENT_NAMES.get(symbol)
        return f"{name} {symbol}" if name else symbol
    return symbol


def market_symbol(instrument_id: str | None) -> str:
    if not instrument_id:
        return ""
    value = instrument_id.strip().upper()
    if ":" in value:
        return value.split(":", 1)[1]
    return value


def _market(instrument_id: str | None) -> str:
    if not instrument_id:
        return ""
    value = instrument_id.strip().upper()
    return value.split(":", 1)[0] if ":" in value else ""


def _cn_exchange_suffix(symbol: str) -> str:
    if symbol.startswith(("4", "8")):
        return "BJ"
    if symbol.startswith(("5", "6")):
        return "SH"
    return "SZ"

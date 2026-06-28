CN_INSTRUMENT_NAMES = {
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
    "THEME:SEMICONDUCTOR": "半导体芯片主题",
    "THEME:MEMORY": "存储芯片主题",
    "THEME:AI_COMPUTE": "AI算力供应链主题",
}

_CN_INSTRUMENT_NAMES_READY = False


def _ensure_cn_instrument_names_loaded() -> None:
    global _CN_INSTRUMENT_NAMES_READY
    if _CN_INSTRUMENT_NAMES_READY:
        return
    prior_ready = _CN_INSTRUMENT_NAMES_READY
    try:
        from qagent.db import initialize_database, create_session_factory
        from qagent.storage.repository import QagentRepository
        from qagent.market.tradable import load_cn_tradable_instruments

        initialize_database()
        repo = QagentRepository(create_session_factory())
        instruments = repo.list_tradable_instruments(limit=20_000)
        register_cn_instrument_names({item.symbol: item.name for item in instruments})
        if not instruments:
            catalog = load_cn_tradable_instruments(include_full_etfs=True, use_cache=True)
            register_cn_instrument_names({item.symbol: item.name for item in catalog.items})
    except Exception:
        if not prior_ready:
            _CN_INSTRUMENT_NAMES_READY = False
        raise
    else:
        _CN_INSTRUMENT_NAMES_READY = True


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
        _ensure_cn_instrument_names_loaded()
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
        value = value.split(":", 1)[1]
    exchange = _cn_exchange_suffix(value)
    if exchange and _looks_like_cn_code(value):
        return value.rsplit(".", 1)[0]
    return value


def _market(instrument_id: str | None) -> str:
    if not instrument_id:
        return ""
    value = instrument_id.strip().upper()
    if ":" in value:
        return value.split(":", 1)[0]
    return "CN" if _looks_like_cn_code(value) else ""


def _looks_like_cn_code(value: str) -> bool:
    if not value:
        return False
    normalized = value.split(".", 1)[0]
    return len(normalized) == 6 and normalized.isdigit()


def _cn_exchange_suffix(symbol: str) -> str:
    if symbol.startswith(("4", "8", "920")):
        return "BJ"
    if symbol.startswith(("5", "6")):
        return "SH"
    return "SZ"

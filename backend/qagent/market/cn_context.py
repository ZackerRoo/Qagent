from dataclasses import dataclass

from qagent.domain.models import MarketContext
from qagent.recommendations.cn_execution import build_trading_constraints


UNKNOWN_ETF_EXPOSURE = "未知ETF暴露"
UNKNOWN_STOCK_INDUSTRY = "未知个股行业"


@dataclass(frozen=True)
class EtfExposure:
    group: str
    benchmark: str
    theme: str


_ETF_EXPOSURE_RULES: tuple[tuple[EtfExposure, tuple[str, ...]], ...] = (
    # Cash, fixed income, and commodities are checked first because their names
    # can also contain broad words such as finance, energy, or value.
    (
        EtfExposure("货币ETF", "货币基金", "现金管理"),
        (
            "货币ETF",
            "现金ETF",
            "添富快线",
            "招商快线",
            "快钱ETF",
            "日日鑫",
            "添利ETF",
            "财富宝ETF",
            "银华日利",
            "华宝添益",
            "保证金",
        ),
    ),
    (EtfExposure("债券ETF:可转债", "可转债", "固定收益"), ("可转债", "转债ETF")),
    (EtfExposure("债券ETF:国债", "国债", "固定收益"), ("国债", "国开债", "政金债")),
    (EtfExposure("债券ETF:信用债", "信用债", "固定收益"), ("信用债", "城投债", "公司债")),
    (EtfExposure("债券ETF:综合", "债券", "固定收益"), ("债券ETF", "债券基金ETF")),
    (EtfExposure("商品ETF:黄金", "黄金", "商品配置"), ("黄金", "上海金", "金ETF")),
    (EtfExposure("商品ETF:白银", "白银", "商品配置"), ("白银",)),
    (EtfExposure("商品ETF:能源化工", "能源化工", "商品配置"), ("豆粕", "能源化工", "商品ETF")),
    # Cross-border exposures must precede domestic sector and factor rules.
    (
        EtfExposure("跨境ETF:中概互联网", "中概互联网", "跨境配置"),
        ("中概互联网", "中国互联网", "中概互联"),
    ),
    (
        EtfExposure("跨境ETF:港股科技", "港股科技", "跨境配置"),
        (
            "恒生科技",
            "港股科技",
            "香港科技",
            "港股通科技",
            "港股互联网",
            "沪港深科技",
            "沪深港科技",
        ),
    ),
    (
        EtfExposure("跨境ETF:港股医药", "港股医药", "跨境配置"),
        ("恒生医药", "港股医药", "港股创新药"),
    ),
    (EtfExposure("跨境ETF:港股消费", "港股消费", "跨境配置"), ("恒生消费", "港股消费")),
    (
        EtfExposure("跨境ETF:港股红利", "港股红利", "跨境配置"),
        ("港股红利", "港股央企红利", "恒生红利", "港股通红利"),
    ),
    (
        EtfExposure("跨境ETF:港股综合", "港股综合", "跨境配置"),
        ("恒生", "港股", "香港大盘", "H股ETF"),
    ),
    (
        EtfExposure("跨境ETF:美国医药", "美国医药", "跨境配置"),
        ("标普生物科技", "标普医疗", "美国医药", "美国生物"),
    ),
    (
        EtfExposure("跨境ETF:美股科技", "美股科技", "跨境配置"),
        ("纳斯达克", "纳指", "美国科技", "标普科技"),
    ),
    (
        EtfExposure("跨境ETF:美国宽基", "美国宽基", "跨境配置"),
        ("标普500", "美国50", "道琼斯", "美国ETF"),
    ),
    (
        EtfExposure("跨境ETF:日本", "日本股票", "跨境配置"),
        ("日经", "日本ETF", "日本东证", "东证ETF"),
    ),
    (EtfExposure("跨境ETF:欧洲", "欧洲股票", "跨境配置"), ("德国", "法国", "英国", "欧洲ETF")),
    (EtfExposure("跨境ETF:印度", "印度股票", "跨境配置"), ("印度",)),
    (
        EtfExposure("跨境ETF:东南亚", "东南亚股票", "跨境配置"),
        ("越南", "东南亚", "新加坡", "亚太精选", "新兴亚洲"),
    ),
    (EtfExposure("跨境ETF:巴西", "巴西股票", "跨境配置"), ("巴西",)),
    (EtfExposure("跨境ETF:中东", "中东股票", "跨境配置"), ("沙特", "中东")),
    (EtfExposure("跨境ETF:韩国", "韩国股票", "跨境配置"), ("韩国",)),
    # Domestic sector groups intentionally share names with stock industries so
    # a sector ETF and stocks from that sector consume the same risk capacity.
    (EtfExposure("银行", "银行行业", "大金融"), ("银行",)),
    (
        EtfExposure("证券/金融服务", "证券行业", "大金融"),
        ("证券", "券商", "金融科技", "金融ETF"),
    ),
    (EtfExposure("半导体", "半导体行业", "国产替代"), ("半导体", "芯片", "集成电路")),
    (
        EtfExposure("人工智能/计算机", "人工智能与计算机", "数字经济"),
        (
            "人工智能",
            "AI ETF",
            "AIETF",
            "算力",
            "云计算",
            "大数据",
            "软件",
            "计算机",
            "数字经济",
            "信创",
            "工业互联网",
            "互联网ETF",
            "互联网龙头",
            "信息安全",
            "信息技术",
            "物联网",
            "科技龙头",
            "科技先锋",
            "TMT",
            "VRETF",
            "科创信息",
        ),
    ),
    (
        EtfExposure("通信设备", "通信行业", "数字经济"),
        ("通信", "电信ETF", "5G", "光通信", "卫星"),
    ),
    (EtfExposure("机器人/自动化", "机器人行业", "先进制造"), ("机器人", "自动化")),
    (
        EtfExposure("电子", "电子行业", "先进制造"),
        ("电子ETF", "电子50", "消费电子", "消电ETF", "元器件"),
    ),
    (EtfExposure("白酒", "白酒行业", "消费"), ("白酒", "酒ETF")),
    (EtfExposure("消费", "消费行业", "消费"), ("消费", "食品饮料", "食品", "家电", "零售")),
    (
        EtfExposure("医药生物", "医药行业", "医疗健康"),
        ("医药", "医疗", "创新药", "生物科技", "中药", "药ETF", "疫苗"),
    ),
    (
        EtfExposure("新能源", "新能源行业", "绿色转型"),
        ("新能源", "绿色能源", "光伏", "储能", "电池", "锂电", "风电"),
    ),
    (
        EtfExposure("汽车", "汽车行业", "先进制造"),
        ("汽车", "新能源车", "智能车", "智能电车", "智能电动", "智能驾驶"),
    ),
    (EtfExposure("国防军工", "国防军工", "先进制造"), ("军工", "国防", "航空航天", "航天")),
    (EtfExposure("有色金属", "有色金属行业", "资源品"), ("有色", "稀土", "稀有金属", "工业金属")),
    (EtfExposure("煤炭", "煤炭行业", "资源品"), ("煤炭",)),
    (
        EtfExposure("石油石化", "石油石化行业", "资源品"),
        ("油气", "石油石化", "石油", "石化ETF"),
    ),
    (EtfExposure("钢铁", "钢铁行业", "资源品"), ("钢铁",)),
    (EtfExposure("基础化工", "化工行业", "资源品"), ("化工", "化学", "新材料")),
    (EtfExposure("能源", "能源行业", "资源品"), ("能源ETF",)),
    (EtfExposure("资源品", "资源行业", "资源品"), ("资源ETF", "矿业")),
    (EtfExposure("基础材料", "材料行业", "资源品"), ("材料ETF",)),
    (EtfExposure("农林牧渔", "农林牧渔行业", "农业"), ("农业", "农牧", "养殖", "畜牧", "粮食")),
    (EtfExposure("房地产", "房地产行业", "地产"), ("房地产", "地产ETF")),
    (EtfExposure("传媒娱乐", "传媒娱乐行业", "数字经济"), ("传媒", "游戏", "动漫", "影视")),
    (EtfExposure("电力公用", "电力公用行业", "绿色转型"), ("电力", "绿电", "公用事业")),
    (EtfExposure("基建建材", "基建建材行业", "基建"), ("基建", "建筑", "建材", "工程机械")),
    (
        EtfExposure("交通运输", "交通运输行业", "出行"),
        ("交通运输", "交运ETF", "物流", "航空", "机场"),
    ),
    (EtfExposure("旅游", "旅游行业", "消费"), ("旅游",)),
    (EtfExposure("环保", "环保行业", "绿色转型"), ("环保", "碳中和")),
    (EtfExposure("电力设备", "电力设备行业", "先进制造"), ("电网设备",)),
    (
        EtfExposure("机械设备", "机械设备行业", "先进制造"),
        ("高端装备", "高端制造", "智能制造", "机床", "工业母机", "机械ETF", "科创机械"),
    ),
    (EtfExposure("教育", "教育行业", "消费"), ("教育ETF",)),
    (EtfExposure("船舶制造", "船舶制造行业", "先进制造"), ("船舶ETF",)),
    # Strategy indexes precede broad indexes so, for example, an HS300 dividend
    # ETF is constrained with other dividend products instead of generic HS300.
    (EtfExposure("策略ETF:自由现金流", "自由现金流", "质量价值"), ("自由现金流", "现金流")),
    (EtfExposure("策略ETF:红利", "红利指数", "低估值红利"), ("红利", "股息")),
    (EtfExposure("策略ETF:低波动", "低波动指数", "低波动"), ("低波", "低波动")),
    (EtfExposure("策略ETF:质量", "质量指数", "质量价值"), ("质量", "基本面")),
    (EtfExposure("策略ETF:央企国企", "央企国企指数", "国企改革"), ("央企", "国企")),
    (EtfExposure("策略ETF:ESG", "ESG指数", "ESG"), ("ESG", "社会责任")),
    (
        EtfExposure("策略ETF:可持续", "可持续发展", "ESG"),
        ("可持续发展", "低碳ETF", "长江保护"),
    ),
    (EtfExposure("策略ETF:民企", "民营企业指数", "民企"), ("民企ETF",)),
    (EtfExposure("策略ETF:治理", "治理指数", "公司治理"), ("治理ETF", "责任ETF")),
    (EtfExposure("策略ETF:养老", "养老产业指数", "养老"), ("养老ETF",)),
    (EtfExposure("主题ETF:一带一路", "一带一路", "区域发展"), ("一带一路",)),
    (
        EtfExposure("主题ETF:区域发展", "区域发展", "区域发展"),
        (
            "成渝经济圈",
            "湖北ETF",
            "长三角",
            "杭州湾区",
            "大湾区",
            "湾创ETF",
            "张江ETF",
            "之江凤凰",
        ),
    ),
    (EtfExposure("策略ETF:价值", "价值指数", "质量价值"), ("价值ETF", "价值指数")),
    (EtfExposure("策略ETF:成长", "成长指数", "成长"), ("成长ETF", "成长指数")),
    # Domestic broad indexes are deliberately grouped by benchmark family.
    (
        EtfExposure("宽基ETF:中证A50", "中证A50", "宽基指数"),
        ("中证A50", "MSCI中国A50", "富时中国A50", "A50ETF", "A50增强"),
    ),
    (
        EtfExposure("宽基ETF:中证A500", "中证A500", "宽基指数"),
        ("中证A500", "A500ETF", "A500增强"),
    ),
    (
        EtfExposure("宽基ETF:中证A100", "中证A100", "宽基指数"),
        ("中证A100", "A100ETF"),
    ),
    (EtfExposure("宽基ETF:上证50", "上证50", "宽基指数"), ("上证50",)),
    (EtfExposure("宽基ETF:沪深300", "沪深300", "宽基指数"), ("沪深300", "300ETF")),
    (
        EtfExposure("宽基ETF:中证500", "中证500", "宽基指数"),
        ("中证500", "500ETF", "500增强"),
    ),
    (EtfExposure("宽基ETF:中证800", "中证800", "宽基指数"), ("中证800",)),
    (
        EtfExposure("宽基ETF:中证1000", "中证1000", "宽基指数"),
        ("中证1000", "1000ETF", "1000增强"),
    ),
    (EtfExposure("宽基ETF:中证2000", "中证2000", "宽基指数"), ("中证2000", "2000ETF")),
    (EtfExposure("宽基ETF:国证2000", "国证2000", "宽基指数"), ("国证2000",)),
    (
        EtfExposure("宽基ETF:创业板50", "创业板50", "宽基指数"),
        ("创业板50", "创50ETF"),
    ),
    (
        EtfExposure("宽基ETF:科创创业", "科创创业50", "宽基指数"),
        ("科创创业", "双创50"),
    ),
    (
        EtfExposure("宽基ETF:创业板", "创业板", "宽基指数"),
        ("创业板", "创业综指", "创业大盘", "创100ETF", "创中盘", "中创400", "创成长"),
    ),
    (EtfExposure("宽基ETF:科创50", "科创50", "宽基指数"), ("科创50",)),
    (EtfExposure("宽基ETF:科创100", "科创100", "宽基指数"), ("科创100",)),
    (EtfExposure("宽基ETF:科创200", "科创200", "宽基指数"), ("科创200",)),
    (
        EtfExposure("宽基ETF:科创板", "科创板", "宽基指数"),
        ("科创综指", "科创增强", "科创板", "科创ETF"),
    ),
    (
        EtfExposure("宽基ETF:深证", "深证宽基", "宽基指数"),
        (
            "深证100",
            "深100ETF",
            "深证50",
            "深证主板50",
            "深证成指",
            "深成ETF",
            "中小100",
        ),
    ),
    (
        EtfExposure("宽基ETF:上证", "上证宽基", "宽基指数"),
        ("上证180", "上证380", "上证580", "上证中盘", "上证增强", "上证指数", "超大盘ETF"),
    ),
    (
        EtfExposure("宽基ETF:全市场", "A股全市场", "宽基指数"),
        (
            "MSCI A股",
            "MSCI中国A股",
            "MSCI中国ETF",
            "A股ETF",
            "全市场",
            "全指ETF",
            "宽基ETF",
        ),
    ),
)


KNOWN_CONTEXT: dict[str, dict[str, list[str] | str]] = {
    "000001": {
        "industry": "银行",
        "themes": ["大金融", "低估值红利"],
        "index_memberships": ["沪深300"],
    },
    "000063": {
        "industry": "通信设备",
        "themes": ["AI算力供应链", "5G", "国产替代"],
        "index_memberships": ["沪深300"],
    },
    "002156": {
        "industry": "半导体封测",
        "themes": ["存储芯片", "先进封装", "国产替代"],
        "index_memberships": ["中证1000"],
    },
    "002281": {
        "industry": "光通信",
        "themes": ["AI算力供应链", "CPO", "光通信"],
        "index_memberships": ["中证1000"],
    },
    "002371": {
        "industry": "半导体设备",
        "themes": ["国产替代", "先进制程", "芯片设备"],
        "index_memberships": ["沪深300"],
    },
    "300033": {
        "industry": "金融科技",
        "themes": ["证券IT", "AI应用"],
        "index_memberships": ["创业板指"],
    },
    "300059": {
        "industry": "互联网券商",
        "themes": ["大金融", "金融科技"],
        "index_memberships": ["创业板50", "沪深300"],
    },
    "300750": {
        "industry": "电池",
        "themes": ["新能源车", "储能"],
        "index_memberships": ["创业板50", "沪深300"],
    },
    "600519": {
        "industry": "白酒",
        "themes": ["消费龙头", "核心资产"],
        "index_memberships": ["沪深300"],
    },
    "688981": {
        "industry": "半导体",
        "themes": ["AI算力供应链", "国产替代", "晶圆代工"],
        "index_memberships": ["科创50"],
    },
    "688012": {
        "industry": "半导体设备",
        "themes": ["国产替代", "先进制程", "芯片设备"],
        "index_memberships": ["科创50"],
    },
    "688126": {
        "industry": "半导体材料",
        "themes": ["国产替代", "大硅片", "芯片材料"],
        "index_memberships": ["科创50"],
    },
    "688008": {
        "industry": "存储芯片",
        "themes": ["存储芯片", "HBM", "国产替代"],
        "index_memberships": ["科创50"],
    },
    "603986": {
        "industry": "存储芯片",
        "themes": ["存储芯片", "MCU", "国产替代"],
        "index_memberships": ["沪深300"],
    },
    "688525": {
        "industry": "存储芯片",
        "themes": ["存储芯片", "HBM", "国产替代"],
        "index_memberships": ["科创板"],
    },
    "301308": {
        "industry": "存储芯片",
        "themes": ["存储芯片", "企业级存储", "国产替代"],
        "index_memberships": ["创业板"],
    },
    "688041": {
        "industry": "AI芯片",
        "themes": ["AI算力供应链", "国产替代", "CPU/GPU"],
        "index_memberships": ["科创50"],
    },
    "688256": {
        "industry": "AI芯片",
        "themes": ["AI算力供应链", "国产替代", "AI加速器"],
        "index_memberships": ["科创50"],
    },
    "300308": {
        "industry": "光模块",
        "themes": ["AI算力供应链", "CPO", "光通信"],
        "index_memberships": ["中证1000"],
    },
    "300394": {
        "industry": "光通信",
        "themes": ["AI算力供应链", "CPO", "光通信"],
        "index_memberships": ["创业板"],
    },
    "300475": {
        "industry": "存储芯片",
        "themes": ["存储芯片", "HBM", "国产替代"],
        "index_memberships": ["创业板"],
    },
    "300502": {
        "industry": "光模块",
        "themes": ["AI算力供应链", "CPO", "光通信"],
        "index_memberships": ["创业板"],
    },
    "300223": {
        "industry": "存储芯片",
        "themes": ["存储芯片", "MCU", "国产替代"],
        "index_memberships": ["创业板"],
    },
    "603019": {
        "industry": "AI服务器",
        "themes": ["AI算力供应链", "国产服务器", "数据中心"],
        "index_memberships": ["中证500"],
    },
    "688347": {
        "industry": "晶圆代工",
        "themes": ["半导体", "国产替代", "先进制程"],
        "index_memberships": ["科创板"],
    },
    "588000": {
        "industry": "指数ETF",
        "themes": ["科创板", "硬科技"],
        "index_memberships": ["科创50ETF"],
    },
    "510300": {
        "industry": "指数ETF",
        "themes": ["大盘蓝筹", "指数工具"],
        "index_memberships": ["沪深300ETF"],
    },
    "510500": {
        "industry": "指数ETF",
        "themes": ["中盘成长", "指数工具"],
        "index_memberships": ["中证500ETF"],
    },
    "512100": {
        "industry": "指数ETF",
        "themes": ["小盘成长", "指数工具"],
        "index_memberships": ["中证1000ETF"],
    },
    "159949": {
        "industry": "指数ETF",
        "themes": ["创业板", "成长股", "指数工具"],
        "index_memberships": ["创业板50ETF"],
    },
}


def build_market_context(
    instrument_id: str,
    instrument_label: str | None = None,
) -> MarketContext | None:
    if not instrument_id.startswith("CN:"):
        return None

    symbol = instrument_id.split(":", 1)[1]
    constraints = build_trading_constraints(instrument_id, instrument_label)
    board = constraints.board if constraints else "A股"
    known = KNOWN_CONTEXT.get(symbol, {})
    known_industry = str(known.get("industry") or "").strip()
    etf_exposure = (
        infer_etf_exposure(instrument_label, current_industry=known_industry)
        if board == "ETF" or "ETF" in (instrument_label or "").upper()
        else None
    )
    industry = (
        etf_exposure.group
        if etf_exposure is not None
        else known_industry or _infer_industry(symbol, instrument_label, board)
    )
    themes = _dedupe(
        [
            *_as_list(known.get("themes")),
            *_infer_themes(symbol, instrument_label, industry, board),
            *([etf_exposure.theme] if etf_exposure is not None else []),
        ]
    )
    memberships = _as_list(known.get("index_memberships")) or _infer_index_memberships(
        symbol,
        board,
        instrument_label,
        etf_exposure,
    )

    parts = [industry]
    if themes:
        parts.append("、".join(themes[:3]))
    if memberships:
        parts.append("成分/跟踪：" + "、".join(memberships[:2]))
    return MarketContext(
        board=board,
        industry=industry,
        themes=themes,
        index_memberships=memberships,
        summary="；".join(parts),
    )


def _as_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def _infer_industry(symbol: str, label: str | None, board: str) -> str:
    text = label or ""
    if "银行" in text:
        return "银行"
    if "证券" in text or "财富" in text:
        return "证券/金融服务"
    if "芯" in text or "半导体" in text:
        return "半导体"
    if "ETF" in text.upper() or board == "ETF":
        exposure = infer_etf_exposure(text)
        return exposure.group if exposure is not None else UNKNOWN_ETF_EXPOSURE
    if symbol.startswith("688"):
        return "硬科技"
    if symbol.startswith(("300", "301")):
        return "成长制造"
    # A board or numeric prefix is not an industry. Keep the missing taxonomy
    # visible so a downstream concentration report cannot merge unrelated stocks.
    return UNKNOWN_STOCK_INDUSTRY


def _infer_themes(symbol: str, label: str | None, industry: str, board: str) -> list[str]:
    themes: list[str] = []
    text = label or ""
    if industry in {"半导体", "硬科技"}:
        themes.extend(["AI算力供应链", "国产替代"])
    if industry == "银行":
        themes.extend(["大金融", "低估值红利"])
    if board == "科创板":
        themes.append("科创板")
    if board == "创业板":
        themes.append("成长股")
    if board == "北交所":
        themes.append("专精特新")
    if "ETF" in text.upper():
        themes.append("指数工具")
    return _dedupe(themes)


def _infer_index_memberships(
    symbol: str,
    board: str,
    label: str | None = None,
    etf_exposure: EtfExposure | None = None,
) -> list[str]:
    if board == "科创板":
        return ["科创板"]
    if board == "创业板":
        return ["创业板"]
    if board == "ETF":
        exposure = etf_exposure or infer_etf_exposure(label)
        return [exposure.benchmark] if exposure is not None else []
    return []


def infer_etf_exposure(
    instrument_label: str | None,
    *,
    current_industry: str | None = None,
) -> EtfExposure | None:
    """Return a deterministic economic exposure for a named ETF.

    Existing specific industries remain authoritative. Generic ETF labels are
    reclassified from the instrument name, while unrecognized products return
    None so downstream paper admission can fail closed.
    """

    current = str(current_industry or "").strip()
    if current and current.lower() not in {
        "指数etf",
        "etf",
        "unknown",
        "unclassified",
        "未知",
        UNKNOWN_ETF_EXPOSURE.lower(),
    }:
        return EtfExposure(current, f"{current}ETF", current)

    text = "".join(str(instrument_label or "").upper().split())
    if not text:
        return None
    if text.startswith("科技ETF") or "创科技ETF" in text:
        return EtfExposure("人工智能/计算机", "科技行业", "数字经济")
    for exposure, keywords in _ETF_EXPOSURE_RULES:
        if any("".join(keyword.upper().split()) in text for keyword in keywords):
            return exposure
    return None


def infer_etf_exposure_group(
    instrument_label: str | None,
    *,
    current_industry: str | None = None,
) -> str | None:
    exposure = infer_etf_exposure(
        instrument_label,
        current_industry=current_industry,
    )
    return exposure.group if exposure is not None else None


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result

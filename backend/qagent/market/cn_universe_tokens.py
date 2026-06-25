from dataclasses import dataclass, field
from typing import Literal

import akshare as ak
import pandas as pd
from pydantic import BaseModel, Field

from qagent.market.instruments import register_cn_instrument_names


ProviderKind = Literal["csindex", "sina"]


@dataclass(frozen=True)
class IndexUniverseDefinition:
    token: str
    label: str
    index_code: str
    provider: ProviderKind
    fallback_symbols: list[str]
    fallback_names: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EtfUniverseDefinition:
    token: str
    label: str
    symbols: list[str]
    names: dict[str, str]


@dataclass(frozen=True)
class ThemeUniverseDefinition:
    token: str
    label: str
    concept_names: list[str]
    fallback_symbols: list[str]
    fallback_names: dict[str, str]


class CnUniverseTokenResolution(BaseModel):
    symbols: list[str]
    names: dict[str, str] = Field(default_factory=dict)
    data_health: dict[str, str] = Field(default_factory=dict)
    is_dynamic: bool = False


INDEX_UNIVERSES: dict[str, IndexUniverseDefinition] = {
    "CN:INDEX:KCB50": IndexUniverseDefinition(
        token="CN:INDEX:KCB50",
        label="科创50成分股",
        index_code="000688",
        provider="csindex",
        fallback_symbols=["688981", "688111", "688012", "688008", "688041", "688256"],
        fallback_names={
            "688981": "中芯国际",
            "688111": "金山办公",
            "688012": "中微公司",
            "688008": "澜起科技",
            "688041": "海光信息",
            "688256": "寒武纪",
        },
    ),
    "CN:INDEX:CSI300": IndexUniverseDefinition(
        token="CN:INDEX:CSI300",
        label="沪深300成分股",
        index_code="000300",
        provider="csindex",
        fallback_symbols=["600519", "300750", "601318", "600036", "000001", "000858"],
        fallback_names={
            "600519": "贵州茅台",
            "300750": "宁德时代",
            "601318": "中国平安",
            "600036": "招商银行",
            "000001": "平安银行",
            "000858": "五粮液",
        },
    ),
    "CN:INDEX:CSI500": IndexUniverseDefinition(
        token="CN:INDEX:CSI500",
        label="中证500成分股",
        index_code="000905",
        provider="csindex",
        fallback_symbols=["600570", "600276", "600690", "600887", "300033", "300059"],
        fallback_names={
            "600570": "恒生电子",
            "600276": "恒瑞医药",
            "600690": "海尔智家",
            "600887": "伊利股份",
            "300033": "同花顺",
            "300059": "东方财富",
        },
    ),
    "CN:INDEX:CSI1000": IndexUniverseDefinition(
        token="CN:INDEX:CSI1000",
        label="中证1000成分股",
        index_code="000852",
        provider="csindex",
        fallback_symbols=["300274", "300308", "002241", "002475", "002594", "300124"],
        fallback_names={
            "300274": "阳光电源",
            "300308": "中际旭创",
            "002241": "歌尔股份",
            "002475": "立讯精密",
            "002594": "比亚迪",
            "300124": "汇川技术",
        },
    ),
    "CN:INDEX:CHINEXT50": IndexUniverseDefinition(
        token="CN:INDEX:CHINEXT50",
        label="创业板50成分股",
        index_code="399673",
        provider="sina",
        fallback_symbols=["300750", "300760", "300124", "300059", "300274", "300033"],
        fallback_names={
            "300750": "宁德时代",
            "300760": "迈瑞医疗",
            "300124": "汇川技术",
            "300059": "东方财富",
            "300274": "阳光电源",
            "300033": "同花顺",
        },
    ),
}


ETF_UNIVERSES: dict[str, EtfUniverseDefinition] = {
    "CN:ETF:KCB50": EtfUniverseDefinition(
        token="CN:ETF:KCB50",
        label="科创50ETF",
        symbols=["588000"],
        names={"588000": "科创50ETF"},
    ),
    "CN:ETF:CSI300": EtfUniverseDefinition(
        token="CN:ETF:CSI300",
        label="沪深300ETF",
        symbols=["510300"],
        names={"510300": "沪深300ETF"},
    ),
    "CN:ETF:CSI500": EtfUniverseDefinition(
        token="CN:ETF:CSI500",
        label="中证500ETF",
        symbols=["510500"],
        names={"510500": "中证500ETF"},
    ),
    "CN:ETF:CSI1000": EtfUniverseDefinition(
        token="CN:ETF:CSI1000",
        label="中证1000ETF",
        symbols=["512100"],
        names={"512100": "中证1000ETF"},
    ),
    "CN:ETF:CHINEXT50": EtfUniverseDefinition(
        token="CN:ETF:CHINEXT50",
        label="创业板50ETF",
        symbols=["159949"],
        names={"159949": "创业板50ETF"},
    ),
}


THEME_UNIVERSES: dict[str, ThemeUniverseDefinition] = {
    "CN:THEME:SEMICONDUCTOR": ThemeUniverseDefinition(
        token="CN:THEME:SEMICONDUCTOR",
        label="半导体芯片主题",
        concept_names=["半导体", "芯片概念"],
        fallback_symbols=[
            "688981",
            "688012",
            "688126",
            "688008",
            "688041",
            "688256",
            "603986",
            "002371",
            "002156",
            "300223",
        ],
        fallback_names={
            "688981": "中芯国际",
            "688012": "中微公司",
            "688126": "沪硅产业",
            "688008": "澜起科技",
            "688041": "海光信息",
            "688256": "寒武纪",
            "603986": "兆易创新",
            "002371": "北方华创",
            "002156": "通富微电",
            "300223": "北京君正",
        },
    ),
    "CN:THEME:MEMORY": ThemeUniverseDefinition(
        token="CN:THEME:MEMORY",
        label="存储芯片主题",
        concept_names=["存储芯片", "HBM"],
        fallback_symbols=[
            "688008",
            "603986",
            "688525",
            "301308",
            "300223",
            "000021",
            "001309",
            "300475",
            "002156",
            "688347",
        ],
        fallback_names={
            "688008": "澜起科技",
            "603986": "兆易创新",
            "688525": "佰维存储",
            "301308": "江波龙",
            "300223": "北京君正",
            "000021": "深科技",
            "001309": "德明利",
            "300475": "香农芯创",
            "002156": "通富微电",
            "688347": "华虹公司",
        },
    ),
    "CN:THEME:AI_COMPUTE": ThemeUniverseDefinition(
        token="CN:THEME:AI_COMPUTE",
        label="AI算力供应链主题",
        concept_names=["CPO概念", "算力概念", "人工智能"],
        fallback_symbols=[
            "300308",
            "000063",
            "002281",
            "300502",
            "300394",
            "688041",
            "688256",
            "603019",
            "002415",
            "300124",
        ],
        fallback_names={
            "300308": "中际旭创",
            "000063": "中兴通讯",
            "002281": "光迅科技",
            "300502": "新易盛",
            "300394": "天孚通信",
            "688041": "海光信息",
            "688256": "寒武纪",
            "603019": "中科曙光",
            "002415": "海康威视",
            "300124": "汇川技术",
        },
    ),
}


def is_cn_universe_token(symbol: str) -> bool:
    token = symbol.strip().upper()
    return token in INDEX_UNIVERSES or token in ETF_UNIVERSES or token in THEME_UNIVERSES


def resolve_cn_universe_token(token: str, limit: int) -> CnUniverseTokenResolution:
    normalized = token.strip().upper()
    if normalized in ETF_UNIVERSES:
        return _resolve_etf_token(ETF_UNIVERSES[normalized])
    if normalized in THEME_UNIVERSES:
        return _resolve_theme_token(THEME_UNIVERSES[normalized], limit=limit)
    if normalized not in INDEX_UNIVERSES:
        raise ValueError(f"unsupported China universe token: {token}")
    return _resolve_index_token(INDEX_UNIVERSES[normalized], limit=limit)


def _resolve_index_token(
    definition: IndexUniverseDefinition,
    limit: int,
) -> CnUniverseTokenResolution:
    try:
        raw = _load_index_constituents(definition)
        records = _normalize_constituents(raw)
        if not records:
            raise ValueError("empty index constituents")
        limited = records[: max(limit, 0)]
        names = {record["symbol"]: record["name"] for record in records if record["name"]}
        register_cn_instrument_names(names)
        return CnUniverseTokenResolution(
            symbols=[f"CN:{record['symbol']}" for record in limited],
            names=names,
            data_health={
                "universe": definition.token,
                "universe_label": definition.label,
                "universe_source": f"akshare_index_stock_cons_{definition.provider}",
                "universe_selected": str(len(limited)),
                "universe_limit": str(limit),
            },
            is_dynamic=True,
        )
    except Exception as exc:
        fallback = definition.fallback_symbols[: max(limit, 0)]
        register_cn_instrument_names(definition.fallback_names)
        return CnUniverseTokenResolution(
            symbols=[f"CN:{symbol}" for symbol in fallback],
            names=definition.fallback_names,
            data_health={
                "universe": definition.token,
                "universe_label": definition.label,
                "universe_source": "fallback",
                "universe_selected": str(len(fallback)),
                "universe_limit": str(limit),
                "universe_fallback": "builtin_index_representative",
                "universe_error": str(exc),
            },
            is_dynamic=True,
        )


def _resolve_etf_token(definition: EtfUniverseDefinition) -> CnUniverseTokenResolution:
    register_cn_instrument_names(definition.names)
    return CnUniverseTokenResolution(
        symbols=[f"CN:{symbol}" for symbol in definition.symbols],
        names=definition.names,
        data_health={
            "universe": definition.token,
            "universe_label": definition.label,
            "universe_source": "builtin_etf",
            "universe_selected": str(len(definition.symbols)),
        },
        is_dynamic=False,
    )


def _resolve_theme_token(
    definition: ThemeUniverseDefinition,
    limit: int,
) -> CnUniverseTokenResolution:
    errors: list[str] = []
    records: list[dict[str, str]] = []
    for concept_name in definition.concept_names:
        try:
            raw = ak.stock_board_concept_cons_em(symbol=concept_name)
            records.extend(_normalize_constituents(raw))
            if records:
                break
        except Exception as exc:
            errors.append(str(exc))
    if records:
        limited = _dedupe_records(records)[: max(limit, 0)]
        names = {record["symbol"]: record["name"] for record in records if record["name"]}
        register_cn_instrument_names(names)
        data_health = {
            "universe": definition.token,
            "universe_label": definition.label,
            "universe_source": "akshare_stock_board_concept_cons_em",
            "universe_theme": ",".join(definition.concept_names),
            "universe_selected": str(len(limited)),
            "universe_limit": str(limit),
        }
        if errors:
            data_health["universe_warnings"] = " | ".join(errors[:3])
        return CnUniverseTokenResolution(
            symbols=[f"CN:{record['symbol']}" for record in limited],
            names=names,
            data_health=data_health,
            is_dynamic=True,
        )

    fallback = definition.fallback_symbols[: max(limit, 0)]
    register_cn_instrument_names(definition.fallback_names)
    data_health = {
        "universe": definition.token,
        "universe_label": definition.label,
        "universe_source": "fallback",
        "universe_selected": str(len(fallback)),
        "universe_limit": str(limit),
        "universe_fallback": "builtin_theme_representative",
    }
    if errors:
        data_health["universe_error"] = " | ".join(errors[:3])
    return CnUniverseTokenResolution(
        symbols=[f"CN:{symbol}" for symbol in fallback],
        names=definition.fallback_names,
        data_health=data_health,
        is_dynamic=True,
    )


def _load_index_constituents(definition: IndexUniverseDefinition) -> pd.DataFrame:
    if definition.provider == "csindex":
        return ak.index_stock_cons_csindex(symbol=definition.index_code)
    return ak.index_stock_cons(symbol=definition.index_code)


def _normalize_constituents(raw: pd.DataFrame) -> list[dict[str, str]]:
    if raw.empty:
        return []
    code_col = _column(
        raw,
        ["品种代码", "成分券代码", "证券代码", "代码", "stock_code", "cons_code"],
    )
    name_col = _optional_column(
        raw,
        ["品种名称", "成分券名称", "证券简称", "名称", "stock_name", "cons_name"],
    )
    records = []
    seen = set()
    for _, row in raw.iterrows():
        symbol = _symbol(row.get(code_col))
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        records.append(
            {
                "symbol": symbol,
                "name": _text(row.get(name_col)) if name_col else "",
            }
        )
    return records


def _dedupe_records(records: list[dict[str, str]]) -> list[dict[str, str]]:
    result = []
    seen = set()
    for record in records:
        symbol = record["symbol"]
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        result.append(record)
    return result


def _column(frame: pd.DataFrame, candidates: list[str]) -> str:
    normalized = {str(column).strip().lower(): str(column) for column in frame.columns}
    for candidate in candidates:
        key = candidate.strip().lower()
        if key in normalized:
            return normalized[key]
    raise ValueError(f"missing required index constituent column: {candidates[0]}")


def _optional_column(frame: pd.DataFrame, candidates: list[str]) -> str | None:
    normalized = {str(column).strip().lower(): str(column) for column in frame.columns}
    for candidate in candidates:
        key = candidate.strip().lower()
        if key in normalized:
            return normalized[key]
    return None


def _symbol(value: object) -> str:
    text = _text(value)
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    digits = "".join(char for char in text if char.isdigit())
    return digits.zfill(6) if digits else ""


def _text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()

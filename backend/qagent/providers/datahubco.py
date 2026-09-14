"""Opt-in, bounded research access to the documented Datahubco HTTP gateway.

The 80-entry source catalogue includes pro_bar, which is not HTTP callable.
HTTP key transport is disabled unless the caller explicitly accepts it;
availability and semantics require verification by each consumer.
"""
from dataclasses import dataclass, field
from datetime import datetime
import json
import math
import re

import httpx

from qagent.providers.tushare_relay import RelayTable

BASE_URL = "http://datahubco.com/app-api/openapi/v1/tushare"
DOCUMENTED_APIS = frozenset("""
daily weekly monthly pro_bar daily_basic new_share top_list top_inst pledge_detail
pledge_stat margin margin_detail repurchase share_float block_trade stk_holdernumber
moneyflow stk_holdertrade stk_limit hk_hold income balancesheet cashflow forecast
express dividend fina_indicator fina_audit fina_mainbz disclosure_date fund_basic
fund_company fund_nav fund_daily fund_div fund_portfolio fund_adj fut_basic trade_cal
fut_daily fut_holding fut_wsr fut_settle index_daily opt_basic opt_daily cb_basic cb_issue
cb_daily fx_obasic fx_daily index_basic index_weekly index_monthly index_weight
index_dailybasic index_classify index_member_all hk_basic shibor shibor_quote shibor_lpr
libor hibor wz_index gz_index tmt_twincome tmt_twincomedetail bo_monthly bo_weekly bo_daily
bo_cinema film_record teleplay_record report_rc cyq_perf cyq_chips stk_rewards
stk_factor_pro stk_nineturn
""".split())
READ_APIS = DOCUMENTED_APIS - {"pro_bar"}
_NAME = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{0,79}\Z")
_SECRET_PARAMS = {"token", "key", "api_key", "authorization", "password", "x_api_key"}


class DatahubcoError(RuntimeError):
    def __init__(self, kind, *, status_code=None):
        self.kind, self.status_code = kind, status_code
        super().__init__(f"datahubco:{kind}")


def _date(value, api):
    if not isinstance(value, str):
        raise DatahubcoError("invalid_date")
    formats = [(r"\d{8}", "%Y%m%d")]
    if api == "teleplay_record":
        formats = [(r"\d{6}", "%Y%m")]
    elif api == "stk_nineturn":
        formats.append((r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", "%Y-%m-%d %H:%M:%S"))
    for pattern, fmt in formats:
        if re.fullmatch(pattern, value):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                break
    raise DatahubcoError("invalid_date")


@dataclass
class DatahubcoClient:
    api_key: str = field(repr=False)
    allow_insecure_http: bool = False
    timeout_seconds: float = 30.0
    transport: httpx.BaseTransport | None = field(default=None, repr=False)

    def __post_init__(self):
        if (not isinstance(self.api_key, str) or not self.api_key
                or any(ord(c) < 33 or ord(c) > 126 for c in self.api_key)):
            raise DatahubcoError("invalid_config")
        if (type(self.allow_insecure_http) is not bool
                or isinstance(self.timeout_seconds, bool)
                or not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 30):
            raise DatahubcoError("invalid_config")

    def query(self, api, *, limit=None, offset=None, fields=None, **params):
        if not isinstance(api, str) or api not in DOCUMENTED_APIS:
            raise DatahubcoError("unknown_api")
        if api not in READ_APIS:
            raise DatahubcoError("http_unsupported_api")
        if not self.allow_insecure_http:
            raise DatahubcoError("insecure_http_not_authorized")
        for value in (limit, offset):
            if value is not None and (type(value) is not int or value < 0):
                raise DatahubcoError("invalid_pagination")
        if limit is not None and limit > 5000:
            raise DatahubcoError("invalid_pagination")
        if offset is not None and limit is None:
            raise DatahubcoError("invalid_pagination")
        clean = {}
        for key, value in params.items():
            if not _NAME.fullmatch(key) or key.lower() in _SECRET_PARAMS:
                raise DatahubcoError("invalid_params")
            if value is None:
                continue
            if (type(value) not in (str, int, float) or len(str(value)) > 4096
                    or (isinstance(value, float) and not math.isfinite(value))
                    or self.api_key in str(value)
                    or (isinstance(value, str) and not value)):
                raise DatahubcoError("invalid_params")
            clean[key] = value
        if ("start_date" in clean) != ("end_date" in clean):
            raise DatahubcoError("date_pair_required")
        if "trade_date" in clean and "start_date" in clean:
            raise DatahubcoError("date_conflict")
        for key in ("start_date", "end_date", "trade_date", "ann_date", "date", "period"):
            if key in clean:
                _date(clean[key], api)
        if "start_date" in clean and _date(clean["start_date"], api) > _date(clean["end_date"], api):
            raise DatahubcoError("invalid_date_range")
        if fields is not None:
            if (not isinstance(fields, str) or not fields or len(fields) > 10000):
                raise DatahubcoError("invalid_fields")
            if self.api_key in fields:
                raise DatahubcoError("invalid_fields")
            names = fields.split(",")
            if len(set(names)) != len(names) or any(not _NAME.fullmatch(n) for n in names):
                raise DatahubcoError("invalid_fields")
            clean["fields"] = fields
        # One bounded page, no automatic pagination or retries.
        row_limit = 5000 if limit is None else limit
        clean["limit"] = row_limit
        if offset is not None:
            clean["offset"] = offset
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=False, verify=True,
                          trust_env=self.transport is None, transport=self.transport) as client:
            try:
                response = client.get(BASE_URL + "/" + api, params=clean,
                                      headers={"X-API-Key": self.api_key})
            except httpx.TransportError:
                raise DatahubcoError("transport_error") from None
        if response.status_code != 200:
            raise DatahubcoError("http_error", status_code=response.status_code)
        try:
            body = response.json()
        except ValueError:
            raise DatahubcoError("invalid_json") from None
        if not isinstance(body, dict) or type(body.get("code")) is not int or body["code"] != 0:
            raise DatahubcoError("upstream_error")
        data = body.get("data")
        if not isinstance(data, dict):
            raise DatahubcoError("table_schema")
        columns, items = data.get("fields"), data.get("items")
        if (not isinstance(columns, list) or any(not isinstance(c, str) or not c for c in columns)
                or len(set(columns)) != len(columns) or not isinstance(items, list)
                or len(items) > row_limit
                or any(not isinstance(row, list) or len(row) != len(columns) for row in items)):
            raise DatahubcoError("table_schema")
        try:
            encoded = json.dumps(data, allow_nan=False)
        except (ValueError, TypeError):
            raise DatahubcoError("table_schema") from None
        if self.api_key in encoded:
            raise DatahubcoError("unsafe_response")
        return RelayTable(api, tuple(columns), tuple(dict(zip(columns, row)) for row in items),
                          row_limit, source="datahubco")

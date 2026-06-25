import type { Language } from "../i18n/catalog";

type Labels = {
  zh: string;
  en: string;
};

type LabelMap = Record<string, Labels>;

const ACTION_LABELS: LabelMap = {
  candidate_entry: { zh: "候选买入", en: "Candidate entry" },
  watch_trigger: { zh: "等待触发", en: "Watch trigger" },
  wait_pullback: { zh: "等待回调", en: "Wait for pullback" },
  avoid: { zh: "暂不追/规避", en: "Avoid for now" },
  pending: { zh: "待定", en: "Pending" },
};

const PROFILE_LABELS: LabelMap = {
  balanced: { zh: "均衡", en: "Balanced" },
  swing: { zh: "波段", en: "Swing" },
  short_term: { zh: "短线", en: "Short-term" },
  growth: { zh: "成长", en: "Growth" },
  conservative: { zh: "保守", en: "Conservative" },
};

const PROFILE_REASON_LABELS: LabelMap = {
  profile_balanced_default: { zh: "综合排序：策略、因子、执行和风险共同评分。", en: "Balanced ranking from strategy, factors, execution, and risk." },
  profile_swing_balanced: { zh: "波段模式更重视盈亏比和可执行价位。", en: "Swing mode emphasizes risk/reward and executable levels." },
  profile_short_term_ready: { zh: "短线模式认为价格接近触发区，适合盯盘确认。", en: "Short-term mode sees price near the trigger zone." },
  profile_short_term_wait: { zh: "短线模式仍需要触发价和量能确认。", en: "Short-term mode still needs trigger and volume confirmation." },
  profile_growth_supported: { zh: "成长模式加权了业绩、上修、TAM 或内生成长策略。", en: "Growth mode boosts earnings, revisions, TAM, or intrinsic growth strategies." },
  profile_growth_not_primary: { zh: "成长模式下不是最强成长主线，需更多基本面确认。", en: "Growth mode needs stronger fundamental confirmation here." },
  profile_conservative_clean: { zh: "保守模式未发现硬性风险否决。", en: "Conservative mode finds no hard risk veto." },
  profile_conservative_risk: { zh: "保守模式因风险否决或数据限制降低排序。", en: "Conservative mode penalizes risk vetoes or data caveats." },
};

const STATUS_LABELS: LabelMap = {
  new_idea: { zh: "新机会", en: "New idea" },
  watch: { zh: "观察", en: "Watch" },
  setup_ready: { zh: "准备", en: "Setup ready" },
  triggered: { zh: "已触发", en: "Triggered" },
  extended: { zh: "偏高", en: "Extended" },
  active: { zh: "进行中", en: "Active" },
  risk_elevated: { zh: "风险升高", en: "Risk elevated" },
  invalidated: { zh: "失效", en: "Invalidated" },
  closed: { zh: "关闭", en: "Closed" },
  postmortem_done: { zh: "已复盘", en: "Reviewed" },
  no_data: { zh: "无数据", en: "No data" },
  no_setup: { zh: "未形成机会", en: "No setup" },
  passed: { zh: "通过", en: "Passed" },
  missing_data: { zh: "缺数据", en: "Missing data" },
  inactive: { zh: "未触发", en: "Inactive" },
  limited_sample: { zh: "样本有限", en: "Limited sample" },
  insufficient_history: { zh: "历史不足", en: "Insufficient history" },
  pending: { zh: "待定", en: "Pending" },
  working: { zh: "跟踪中", en: "Working" },
  lagging: { zh: "走弱", en: "Lagging" },
  open: { zh: "持仓中", en: "Open" },
  target_1_hit: { zh: "目标 1 命中", en: "Target 1 hit" },
  stopped: { zh: "止损", en: "Stopped" },
  time_exit: { zh: "时间退出", en: "Time exit" },
  stop_breached: { zh: "跌破止损", en: "Stop breached" },
  target_reached: { zh: "到达目标", en: "Target reached" },
  clear: { zh: "通过", en: "Clear" },
  warning: { zh: "需警惕", en: "Warning" },
  blocked: { zh: "否决", en: "Blocked" },
  inside_plan: { zh: "计划内", en: "Inside plan" },
  no_price: { zh: "无价格", en: "No price" },
  ready: { zh: "可用", en: "Ready" },
  configured: { zh: "已配置", en: "Configured" },
  missing_config: { zh: "缺配置", en: "Missing config" },
  queued: { zh: "已排队", en: "Queued" },
  sent: { zh: "已发送", en: "Sent" },
  acknowledged: { zh: "已确认", en: "Acknowledged" },
  expired: { zh: "已过期", en: "Expired" },
};

const RISK_VETO_LABELS: LabelMap = {
  poor_risk_reward: { zh: "盈亏比不足", en: "Poor risk/reward" },
  weak_data_quality: { zh: "数据质量不足", en: "Weak data quality" },
  incomplete_trade_plan: { zh: "交易计划不完整", en: "Incomplete trade plan" },
  too_close_to_no_chase: { zh: "接近不追高位", en: "Too close to no-chase level" },
  tight_no_chase_gap: { zh: "追高空间过窄", en: "Tight no-chase gap" },
  low_liquidity: { zh: "流动性偏弱", en: "Low liquidity" },
  overextended: { zh: "短线过热", en: "Overextended" },
  high_volatility: { zh: "波动偏高", en: "High volatility" },
  insufficient_history: { zh: "历史不足", en: "Insufficient history" },
  many_data_caveats: { zh: "数据限制较多", en: "Multiple data caveats" },
};

const RISK_VETO_MESSAGES: LabelMap = {
  poor_risk_reward: { zh: "当前盈亏比不足，不适合作为新买点。", en: "Risk/reward is too low for a new entry." },
  weak_data_quality: { zh: "关键策略数据缺失较多，不能放大仓位。", en: "Too much required strategy data is missing." },
  incomplete_trade_plan: { zh: "触发、止损、目标或不追高价位不完整。", en: "Entry, stop, target, or no-chase level is incomplete." },
  too_close_to_no_chase: { zh: "价格太接近不追高位，追入不划算。", en: "Price is too close to the no-chase level." },
  tight_no_chase_gap: { zh: "入场窗口较窄，等待更清晰确认。", en: "The entry window is narrow; wait for cleaner confirmation." },
  low_liquidity: { zh: "流动性弱，容易滑点或无法按计划退出。", en: "Liquidity is weak; sizing should be avoided or reduced." },
  overextended: { zh: "价格相对趋势支撑偏离较大，不适合追高。", en: "Price is stretched versus trend support." },
  high_volatility: { zh: "近期波动较高，止损可能更容易被噪音触发。", en: "Recent volatility is elevated." },
  insufficient_history: { zh: "价格历史不足，均线结构验证不充分。", en: "Not enough price history to validate trend structure." },
  many_data_caveats: { zh: "数据限制较多，行动前需要复核来源。", en: "Several data caveats are present; verify the source." },
};

const SCAN_BLOCKER_LABELS: LabelMap = {
  no_daily_bars: { zh: "没有日线行情", en: "No daily bars" },
  signal_threshold_not_met: { zh: "信号强度不足", en: "Signal threshold not met" },
  no_active_signals: { zh: "没有活跃信号", en: "No active signals" },
  no_strategy_passed: { zh: "没有策略通过", en: "No strategy passed" },
  strategy_data_missing: { zh: "策略数据缺失", en: "Strategy data missing" },
};

const SCAN_BLOCKER_MESSAGES: LabelMap = {
  no_daily_bars: { zh: "数据源没有返回日线行情，暂时无法判断。", en: "The provider did not return daily OHLCV bars." },
  signal_threshold_not_met: { zh: "信号组合还没有达到机会卡阈值。", en: "The signal stack did not reach the opportunity-card threshold." },
  no_active_signals: { zh: "趋势、回调、突破、量能或涨跌停信号都不活跃。", en: "No trend, pullback, breakout, volume, or limit-status signal is active." },
  no_strategy_passed: { zh: "策略库里没有策略满足前置条件。", en: "No strategy passed its preconditions." },
  strategy_data_missing: { zh: "部分策略输入缺失，需要补齐后再判断。", en: "Some strategy inputs are missing." },
};

const RADAR_SIGNAL_LABELS: LabelMap = {
  approaching_trigger: { zh: "接近买点", en: "Approaching trigger" },
  trigger_breakout: { zh: "突破触发", en: "Trigger breakout" },
  near_stop: { zh: "接近止损", en: "Near stop" },
  near_target: { zh: "接近目标", en: "Near target" },
  volume_surge: { zh: "放量异动", en: "Volume surge" },
  overextended: { zh: "短线过热", en: "Overextended" },
  inside_plan: { zh: "计划内", en: "Inside plan" },
  no_setup: { zh: "未形成机会", en: "No setup" },
};

const RADAR_ACTION_LABELS: LabelMap = {
  approaching_trigger: { zh: "等待触发价和量能确认，不提前抢跑。", en: "Wait for trigger and volume confirmation." },
  trigger_breakout: { zh: "检查不追高位和风险否决后，再按计划处理。", en: "Check no-chase level and risk vetoes before acting." },
  near_stop: { zh: "不要加仓，先确认形态是否失效。", en: "Do not add exposure; verify invalidation." },
  near_target: { zh: "按退出计划处理，可考虑分批或上移止损。", en: "Follow the exit plan; consider partial profit or tighter trailing stop." },
  volume_surge: { zh: "核对是否同时突破触发价。", en: "Check whether price confirms the trigger." },
  overextended: { zh: "避免追高，等待新形态或回调。", en: "Avoid chasing; wait for a new setup or pullback." },
  inside_plan: { zh: "继续跟踪触发、止损、目标和不追高位。", en: "Track trigger, stop, target, and no-chase levels." },
  no_setup: { zh: "先放入观察，查看未推荐原因。", en: "Keep on watchlist; review blockers." },
};

const DIAGNOSTIC_VERDICT_LABELS: LabelMap = {
  effective: { zh: "有效", en: "Effective" },
  watch: { zh: "观察", en: "Watch" },
  weak: { zh: "偏弱", en: "Weak" },
  insufficient_sample: { zh: "样本不足", en: "Insufficient sample" },
};

const DIAGNOSTIC_REASON_LABELS: LabelMap = {
  effective: { zh: "历史复盘显示有正向延续或目标命中表现。", en: "Replay shows positive follow-through or target-hit behavior." },
  watch: { zh: "复盘结果混合，需要当前价格行为和风险控制共同确认。", en: "Replay is mixed; use current price action and risk controls." },
  weak: { zh: "复盘收益偏弱，止损表现没有被目标命中抵消。", en: "Replay is weak; stops are not outweighed by target hits." },
  insufficient_sample: { zh: "样本太少，暂时不能判断策略是否可靠。", en: "Replay sample is too small to judge reliability." },
};

const STRATEGY_LABELS: LabelMap = {
  trend_momentum_stage2: { zh: "二阶段趋势动量", en: "Stage 2 trend momentum" },
  breakout_volume_confirmation: {
    zh: "放量突破确认",
    en: "Breakout with volume confirmation",
  },
  healthy_pullback: { zh: "健康回调", en: "Healthy trend pullback" },
  gf_dma_health: { zh: "GF-DMA 趋势健康", en: "GF-DMA health index" },
  catalyst_financial_transmission: {
    zh: "催化到财务传导",
    en: "Catalyst financial transmission",
  },
  pead_earnings_drift: { zh: "业绩公告后漂移", en: "Post-earnings announcement drift" },
  analyst_revision_momentum: { zh: "分析师上修动量", en: "Analyst revision momentum" },
  tam_adj_peg_growth: {
    zh: "TAM 调整 PEG 成长估值",
    en: "TAM-adjusted PEG growth valuation",
  },
  bayesian_intrinsic_growth: {
    zh: "贝叶斯内生成长估值",
    en: "Bayesian intrinsic growth valuation",
  },
  sector_rotation_regime: { zh: "行业轮动与市场环境", en: "Sector rotation and regime filter" },
  short_squeeze_risk: { zh: "逼空风险监控", en: "Short squeeze risk monitor" },
  options_flow_confirmation: { zh: "期权流确认", en: "Options flow confirmation" },
  insider_institutional_confirmation: {
    zh: "内部人与机构确认",
    en: "Insider and institutional confirmation",
  },
  unclassified: { zh: "未分类", en: "Unclassified" },
};

const STRATEGY_NAME_TO_ID = Object.fromEntries(
  Object.entries(STRATEGY_LABELS).flatMap(([id, labels]) => [
    [labels.en, id],
    [labels.zh, id],
  ]),
);

const FAMILY_LABELS: LabelMap = {
  growth_momentum: { zh: "成长动量", en: "Growth momentum" },
  technical_breakout: { zh: "技术突破", en: "Technical breakout" },
  technical_pullback: { zh: "技术回调", en: "Technical pullback" },
  trend_health: { zh: "趋势健康", en: "Trend health" },
  event_catalyst: { zh: "事件催化", en: "Event catalyst" },
  earnings_momentum: { zh: "业绩动量", en: "Earnings momentum" },
  earnings_revision: { zh: "业绩上修", en: "Earnings revision" },
  growth_valuation: { zh: "成长估值", en: "Growth valuation" },
  market_regime: { zh: "市场环境", en: "Market regime" },
  risk_event: { zh: "风险事件", en: "Risk event" },
  derivatives_confirmation: { zh: "衍生品确认", en: "Derivatives confirmation" },
  ownership_confirmation: { zh: "持有人确认", en: "Ownership confirmation" },
};

const ROLE_LABELS: LabelMap = {
  primary: { zh: "主策略", en: "Primary" },
  risk_control: { zh: "风险控制", en: "Risk control" },
  confirmation: { zh: "确认", en: "Confirmation" },
  valuation: { zh: "估值", en: "Valuation" },
  context: { zh: "环境", en: "Context" },
};

const SIGNAL_LABELS: LabelMap = {
  trend_strength: { zh: "趋势强度", en: "Trend strength" },
  pullback: { zh: "回调", en: "Pullback" },
  breakout: { zh: "突破", en: "Breakout" },
  volume_anomaly: { zh: "量能异动", en: "Volume anomaly" },
  limit_status: { zh: "涨跌停状态", en: "Limit status" },
  event_catalyst: { zh: "事件催化", en: "Event catalyst" },
  event_catalyst_review: { zh: "事件催化复核", en: "Event catalyst review" },
  financial_transmission_review: { zh: "财务传导复核", en: "Financial transmission review" },
  earnings_surprise: { zh: "业绩超预期", en: "Earnings surprise" },
  reasonable_initial_reaction: { zh: "首日反应合理", en: "Reasonable initial reaction" },
  volume_expansion: { zh: "成交量放大", en: "Volume expansion" },
  guidance_raised: { zh: "指引上调", en: "Guidance raised" },
  post_earnings_hold: { zh: "业绩后承接", en: "Post-earnings hold" },
  estimate_revision: { zh: "预期上修", en: "Estimate revision" },
  analyst_rating_balance: { zh: "评级结构", en: "Analyst rating balance" },
  target_price_context: { zh: "目标价背景", en: "Target price context" },
  free_fundamental_growth: { zh: "免费基本面成长数据", en: "Free fundamental growth" },
  valuation_multiples: { zh: "估值倍数", en: "Valuation multiples" },
  tam_proxy: { zh: "TAM 代理变量", en: "TAM proxy" },
  growth_probability_update: { zh: "成长概率更新", en: "Growth probability update" },
  fundamental_growth: { zh: "基本面成长", en: "Fundamental growth" },
  sec_ownership_filing: { zh: "SEC 持有人文件", en: "SEC ownership filing" },
  insider_form: { zh: "内部人表格", en: "Insider form" },
  institutional_filing: { zh: "机构持仓文件", en: "Institutional filing" },
};

const DIRECTION_LABELS: LabelMap = {
  bullish: { zh: "看多", en: "Bullish" },
  bearish: { zh: "看空", en: "Bearish" },
  neutral: { zh: "中性", en: "Neutral" },
};

const FACTOR_LABELS: LabelMap = {
  momentum: { zh: "动量", en: "Momentum" },
  trend_quality: { zh: "趋势质量", en: "Trend quality" },
  liquidity: { zh: "流动性", en: "Liquidity" },
  low_risk: { zh: "低风险", en: "Low risk" },
  reversal: { zh: "回踩/反转", en: "Reversal setup" },
};

const FACTOR_EXPLANATIONS_ZH: Record<string, string> = {
  momentum: "20/60/120 日价格动量在扫描股票池中的排名。",
  trend_quality: "均线排列质量，以及股价相对 20 日均线的位置。",
  liquidity: "20 日平均成交量在扫描股票池中的排名。",
  low_risk: "20 日波动率更低、60 日回撤更浅的股票得分更高。",
  reversal: "强趋势中的短线回踩，或弱势中的反转迹象。",
};

const FACTOR_FLAGS: LabelMap = {
  insufficient_history: { zh: "历史不足", en: "Insufficient history" },
  overextended: { zh: "短线过热", en: "Overextended" },
  high_volatility: { zh: "波动偏高", en: "High volatility" },
  low_liquidity: { zh: "流动性偏弱", en: "Low liquidity" },
};

const ALERT_KIND_LABELS: LabelMap = {
  entry_trigger: { zh: "入场触发", en: "Entry trigger" },
  stop_guard: { zh: "止损保护", en: "Stop guard" },
  target_1_reached: { zh: "目标 1 到达", en: "Target 1 reached" },
};

const CATALYST_LABELS: LabelMap = {
  demand: { zh: "需求", en: "Demand" },
  earnings: { zh: "业绩", en: "Earnings" },
  capital_return: { zh: "股东回报", en: "Capital return" },
  general: { zh: "一般事件", en: "General" },
};

const DATA_REQUIREMENT_LABELS: LabelMap = {
  daily_ohlcv: { zh: "日线行情", en: "Daily OHLCV" },
  signals: { zh: "信号", en: "Signals" },
  fundamentals: { zh: "基本面", en: "Fundamentals" },
  relative_strength: { zh: "相对强度", en: "Relative strength" },
  fundamental_growth: { zh: "基本面增长", en: "Fundamental growth" },
  estimate_revisions: { zh: "预期修正", en: "Estimate revisions" },
  news_events: { zh: "新闻事件", en: "News events" },
  exposure_map: { zh: "业务暴露映射", en: "Exposure map" },
  financial_metrics: { zh: "财务指标", en: "Financial metrics" },
  management_commentary: { zh: "管理层表述", en: "Management commentary" },
  supply_chain_map: { zh: "供应链映射", en: "Supply chain map" },
  earnings_actuals: { zh: "实际业绩", en: "Earnings actuals" },
  earnings_estimates: { zh: "业绩预期", en: "Earnings estimates" },
  announcement_timestamp: { zh: "公告时间", en: "Announcement timestamp" },
  announcement_price_reaction: { zh: "公告日价格反应", en: "Announcement price reaction" },
  guidance: { zh: "业绩指引", en: "Guidance" },
  earnings_transcript: { zh: "业绩会纪要", en: "Earnings transcript" },
  benchmark_returns: { zh: "基准收益", en: "Benchmark returns" },
  analyst_estimates: { zh: "分析师预期", en: "Analyst estimates" },
  revision_timestamps: { zh: "上修时间", en: "Revision timestamps" },
  target_price_revisions: { zh: "目标价上修", en: "Target price revisions" },
  coverage_count: { zh: "覆盖数量", en: "Coverage count" },
  tam_assumptions: { zh: "TAM 假设", en: "TAM assumptions" },
  valuation_multiples: { zh: "估值倍数", en: "Valuation multiples" },
  gross_margin: { zh: "毛利率", en: "Gross margin" },
  net_retention: { zh: "净留存", en: "Net retention" },
  competitive_intensity: { zh: "竞争强度", en: "Competitive intensity" },
  growth_priors: { zh: "成长先验", en: "Growth priors" },
  scenario_probabilities: { zh: "情景概率", en: "Scenario probabilities" },
  unit_economics: { zh: "单位经济模型", en: "Unit economics" },
  sector_constituents: { zh: "行业成分", en: "Sector constituents" },
  sector_breadth: { zh: "行业广度", en: "Sector breadth" },
  macro_factors: { zh: "宏观因子", en: "Macro factors" },
  short_interest: { zh: "空头仓位", en: "Short interest" },
  borrow_rate: { zh: "借券成本", en: "Borrow rate" },
  options_flow: { zh: "期权流", en: "Options flow" },
  limit_status: { zh: "涨跌停状态", en: "Limit status" },
  implied_volatility: { zh: "隐含波动率", en: "Implied volatility" },
  open_interest: { zh: "未平仓量", en: "Open interest" },
  delta: { zh: "Delta", en: "Delta" },
  moneyness: { zh: "价内外程度", en: "Moneyness" },
  dte: { zh: "到期天数", en: "DTE" },
  insider_transactions: { zh: "内部人交易", en: "Insider transactions" },
  institutional_filings: { zh: "机构持仓文件", en: "Institutional filings" },
  buyback_activity: { zh: "回购活动", en: "Buyback activity" },
  float_change: { zh: "流通股变化", en: "Float change" },
  "20d_return": { zh: "20 日收益", en: "20D return" },
  "60d_return": { zh: "60 日收益", en: "60D return" },
  "120d_return": { zh: "120 日收益", en: "120D return" },
};

const DATA_HEALTH_KEYS: LabelMap = {
  provider: { zh: "数据源", en: "Provider" },
  mode: { zh: "模式", en: "Mode" },
  scanned: { zh: "扫描数", en: "Scanned" },
  cards: { zh: "机会卡", en: "Cards" },
  factor_rankings: { zh: "因子排名", en: "Factor rankings" },
  strategy_data_provider: { zh: "策略数据源", en: "Strategy data provider" },
  strategy_filings: { zh: "策略文件", en: "Strategy filings" },
  strategy_announcements: { zh: "公告", en: "Announcements" },
  strategy_fundamentals: { zh: "基本面", en: "Fundamentals" },
  strategy_analyst_insights: { zh: "分析师数据", en: "Analyst insights" },
  market_cache: { zh: "行情缓存", en: "Market cache" },
  market_cache_hits: { zh: "缓存命中", en: "Cache hits" },
  market_cache_misses: { zh: "缓存未命中", en: "Cache misses" },
  market_cache_rows: { zh: "缓存行数", en: "Cache rows" },
  instrument: { zh: "标的", en: "Instrument" },
  symbols: { zh: "标的数", en: "Symbols" },
  bars: { zh: "K线数", en: "Bars" },
  radar_items: { zh: "雷达项", en: "Radar items" },
  diagnostics: { zh: "诊断数", en: "Diagnostics" },
  errors: { zh: "错误", en: "Errors" },
  strategy_data_errors: { zh: "策略数据错误", en: "Strategy data errors" },
  brief_opportunities: { zh: "简报机会", en: "Brief opportunities" },
  brief_entry_watch: { zh: "入场观察", en: "Entry watch" },
  brief_catalysts: { zh: "催化", en: "Catalysts" },
  brief_risk_alerts: { zh: "风险提醒", en: "Risk alerts" },
  brief_validated_strategies: { zh: "已验证策略", en: "Validated strategies" },
  positions: { zh: "持仓", en: "Positions" },
  risk: { zh: "风险", en: "Risk" },
  news: { zh: "新闻", en: "News" },
  hypotheses: { zh: "假设", en: "Hypotheses" },
  trades: { zh: "交易", en: "Trades" },
  active_checked: { zh: "已检查活跃交易", en: "Active checked" },
  rules: { zh: "规则", en: "Rules" },
  instruments: { zh: "标的", en: "Instruments" },
  triggered: { zh: "触发", en: "Triggered" },
  backtest_scans: { zh: "回测扫描", en: "Backtest scans" },
  backtest_signals: { zh: "回测信号", en: "Backtest signals" },
  portfolio_model: { zh: "组合模型", en: "Portfolio model" },
  risk_per_trade_pct: { zh: "单笔风险", en: "Risk per trade" },
  universe: { zh: "股票池", en: "Universe" },
  universe_source: { zh: "股票池来源", en: "Universe source" },
  universe_total: { zh: "全市场数", en: "Universe total" },
  universe_eligible: { zh: "过滤后", en: "Eligible" },
  universe_selected: { zh: "本次扫描", en: "Selected" },
  universe_limit: { zh: "扫描上限", en: "Universe limit" },
  universe_filters: { zh: "过滤条件", en: "Universe filters" },
  universe_excluded: { zh: "剔除统计", en: "Excluded" },
  universe_warnings: { zh: "股票池警告", en: "Universe warnings" },
  universe_fallback: { zh: "降级股票池", en: "Universe fallback" },
  universe_error: { zh: "股票池错误", en: "Universe error" },
  strategy_data_skipped: { zh: "策略数据跳过", en: "Strategy data skipped" },
  brief_news_symbols: { zh: "新闻标的", en: "News symbols" },
  automation_news_scope: { zh: "自动化新闻范围", en: "Automation news scope" },
};

const DATA_HEALTH_VALUES: LabelMap = {
  fixture: { zh: "样例", en: "Fixture" },
  free: { zh: "免费", en: "Free" },
  development: { zh: "开发", en: "Development" },
  development_fixture: { zh: "开发样例", en: "Development fixture" },
  fixture_strategy_data: { zh: "样例策略数据", en: "Fixture strategy data" },
  free_strategy_data: { zh: "免费策略数据", en: "Free strategy data" },
  yfinance: { zh: "yfinance", en: "yfinance" },
  baostock: { zh: "BaoStock", en: "BaoStock" },
  akshare: { zh: "AKShare", en: "AKShare" },
  akshare_etf: { zh: "AKShare ETF", en: "AKShare ETF" },
  "akshare+baostock": { zh: "AKShare + BaoStock", en: "AKShare + BaoStock" },
  enabled: { zh: "已启用", en: "Enabled" },
  CN_ALL: { zh: "全A股候选池", en: "All A-share candidates" },
  "CN:ALL": { zh: "全A股候选池", en: "All A-share candidates" },
  akshare_spot_em: { zh: "AKShare 东方财富实时行情", en: "AKShare Eastmoney spot" },
  akshare_index_stock_cons_csindex: {
    zh: "AKShare 中证指数成分股",
    en: "AKShare CSIndex constituents",
  },
  akshare_index_stock_cons_sina: {
    zh: "AKShare 新浪指数成分股",
    en: "AKShare Sina index constituents",
  },
  builtin_etf: { zh: "内置ETF映射", en: "Built-in ETF map" },
  fallback: { zh: "降级", en: "Fallback" },
  cn_liquid_starter: { zh: "A股30只流动性样本池", en: "A-share liquid starter" },
  skipped_for_dynamic_universe: { zh: "全市场动态池已跳过", en: "Skipped for dynamic universe" },
  ready: { zh: "可用", en: "Ready" },
  configured: { zh: "已配置", en: "Configured" },
  missing_config: { zh: "缺配置", en: "Missing config" },
  true: { zh: "是", en: "true" },
  false: { zh: "否", en: "false" },
  fixed_risk_stop_target_time_exit: {
    zh: "固定风险 + 止损/目标/时间退出",
    en: "Fixed risk stop target time exit",
  },
  "composite strategy data(sec edgar,cninfo)": {
    zh: "组合策略数据（SEC EDGAR、巨潮资讯）",
    en: "Composite strategy data (SEC EDGAR, CNINFO)",
  },
  "composite_strategy_data(sec_edgar,cninfo)": {
    zh: "组合策略数据（SEC EDGAR、巨潮资讯）",
    en: "Composite strategy data (SEC EDGAR, CNINFO)",
  },
};

const PROVIDER_NAME_LABELS: LabelMap = {
  "Fixture data": { zh: "样例数据", en: "Fixture data" },
  "Yahoo Finance via yfinance": { zh: "Yahoo Finance / yfinance", en: "Yahoo Finance via yfinance" },
  "AKShare with BaoStock fallback": {
    zh: "AKShare + BaoStock 备用",
    en: "AKShare with BaoStock fallback",
  },
  "Alpha Vantage": { zh: "Alpha Vantage", en: "Alpha Vantage" },
  "Financial Modeling Prep": { zh: "Financial Modeling Prep", en: "Financial Modeling Prep" },
  Finnhub: { zh: "Finnhub", en: "Finnhub" },
  "SEC EDGAR": { zh: "SEC EDGAR", en: "SEC EDGAR" },
  CNINFO: { zh: "巨潮资讯", en: "CNINFO" },
  Tushare: { zh: "Tushare", en: "Tushare" },
};

const CAPABILITY_LABELS: LabelMap = {
  daily_ohlcv: { zh: "日线行情", en: "Daily OHLCV" },
  us_daily_ohlcv: { zh: "美股日线行情", en: "US daily OHLCV" },
  cn_daily_ohlcv: { zh: "A股日线行情", en: "A-share daily OHLCV" },
  snapshots: { zh: "价格快照", en: "Snapshots" },
  earnings: { zh: "业绩", en: "Earnings" },
  fundamentals: { zh: "基本面", en: "Fundamentals" },
  analyst_revisions: { zh: "分析师上修", en: "Analyst revisions" },
  analyst_estimates: { zh: "分析师预期", en: "Analyst estimates" },
  ratings_snapshot: { zh: "评级快照", en: "Ratings snapshot" },
  price_targets: { zh: "目标价", en: "Price targets" },
  recommendation_trends: { zh: "推荐趋势", en: "Recommendation trends" },
  filings: { zh: "文件", en: "Filings" },
  insider_forms: { zh: "内部人表格", en: "Insider forms" },
  institutional_filings: { zh: "机构持仓文件", en: "Institutional filings" },
  a_share_announcements: { zh: "A股公告", en: "A-share announcements" },
  a_share_financials: { zh: "A股财务", en: "A-share financials" },
  money_flow: { zh: "资金流", en: "Money flow" },
  dragon_tiger: { zh: "龙虎榜", en: "Dragon tiger" },
  limit_status: { zh: "涨跌停状态", en: "Limit status" },
};

const EVIDENCE_KEYS: LabelMap = {
  reason: { zh: "原因", en: "Reason" },
  close: { zh: "收盘价", en: "Close" },
  ma_20: { zh: "20 日均线", en: "20DMA" },
  ma_50: { zh: "50 日均线", en: "50DMA" },
  ma_100: { zh: "100 日均线", en: "100DMA" },
  ma_200: { zh: "200 日均线", en: "200DMA" },
  close_vs_ma_20_pct: { zh: "距 20 日均线", en: "Close vs 20DMA" },
  close_vs_ma_50_pct: { zh: "距 50 日均线", en: "Close vs 50DMA" },
  overextension_penalty: { zh: "过热惩罚", en: "Overextension penalty" },
  trend_score: { zh: "趋势分", en: "Trend score" },
  hypotheses: { zh: "假设数", en: "Hypotheses" },
  max_confidence: { zh: "最高置信度", en: "Max confidence" },
  eps_surprise_pct: { zh: "EPS 超预期", en: "EPS surprise" },
  revenue_surprise_pct: { zh: "收入超预期", en: "Revenue surprise" },
  announcement_return_pct: { zh: "公告日涨跌", en: "Announcement return" },
  volume_ratio: { zh: "量比", en: "Volume ratio" },
  latest_close: { zh: "最新收盘", en: "Latest close" },
  earnings_day_close: { zh: "业绩日收盘", en: "Earnings-day close" },
  earnings_day_low: { zh: "业绩日低点", en: "Earnings-day low" },
  earnings_day_high: { zh: "业绩日高点", en: "Earnings-day high" },
  guidance: { zh: "指引", en: "Guidance" },
  announcement_date: { zh: "公告日", en: "Announcement date" },
  revision_date: { zh: "上修日", en: "Revision date" },
  eps_revision_pct: { zh: "EPS 上修", en: "EPS revision" },
  revenue_revision_pct: { zh: "收入上修", en: "Revenue revision" },
  target_revision_pct: { zh: "目标价上修", en: "Target revision" },
  target_upside_pct: { zh: "目标价空间", en: "Target upside" },
  bullish_rating_ratio: { zh: "看多评级占比", en: "Bullish rating ratio" },
  provider: { zh: "数据源", en: "Provider" },
  as_of_date: { zh: "截至日期", en: "As of date" },
  growth_pct: { zh: "增长率", en: "Growth" },
  margin_pct: { zh: "利润率", en: "Margin" },
  market_cap: { zh: "市值", en: "Market cap" },
  peg_ratio: { zh: "PEG", en: "PEG" },
  tam_assumption_source: { zh: "TAM 假设来源", en: "TAM assumption source" },
  prior_growth_probability: { zh: "成长先验概率", en: "Prior growth probability" },
  posterior_growth_probability: { zh: "成长后验概率", en: "Posterior growth probability" },
  valuation_score: { zh: "估值得分", en: "Valuation score" },
  growth_prior_source: { zh: "成长先验来源", en: "Growth prior source" },
  filings: { zh: "文件数", en: "Filings" },
  insider_forms: { zh: "内部人文件", en: "Insider forms" },
  institutional_filings: { zh: "机构文件", en: "Institutional filings" },
  buyback_related_filings: { zh: "回购相关文件", en: "Buyback-related filings" },
  latest_filing_date: { zh: "最新文件日", en: "Latest filing date" },
  providers: { zh: "数据源", en: "Providers" },
};

const EXACT_TEXT_ZH: Record<string, string> = {
  "Signal stack indicates a watchable setup. Review data caveats before action.":
    "信号栈显示可观察的机会，行动前请检查数据限制。",
  "Signal stack did not meet opportunity-card threshold.":
    "信号组合未达到机会卡阈值。",
  "No daily bars returned by provider.": "数据源没有返回日线行情。",
  "Opportunity card generated.": "已生成机会卡。",
  "Research workflow only; not personalized investment advice.":
    "仅用于研究流程，不是个性化投资建议。",
  "Data quality is reduced by missing strategy inputs or caveats.":
    "由于策略输入缺失或存在数据限制，数据质量被下调。",
  "Price breaks above pivot with volume confirmation.": "价格放量突破枢轴位。",
  "Price holds support and reclaims short-term strength.":
    "价格守住支撑，并重新恢复短线强度。",
  "Post-earnings drift stays above the earnings-day low and clears the earnings-day high.":
    "业绩后走势守住业绩日低点，并突破业绩日高点。",
  "Breakout fails back below pivot/support with weak follow-through.":
    "若价格跌回枢轴/支撑且后续无力，突破失效。",
  "Pullback loses support or closes below the rising 50DMA.":
    "若回调跌破支撑，或收盘跌破上行的 50 日均线，形态失效。",
  "PEAD fails if price loses the earnings-day low or estimates reverse lower.":
    "若价格跌破业绩日低点，或预期重新下修，业绩漂移失效。",
  "After target 1, trail below 10EMA or prior swing low.":
    "到达目标 1 后，止损跟随 10EMA 或前一波段低点。",
  "Trail below 20DMA after the first target.": "到达第一目标后，止损跟随 20 日均线。",
  "After target 1, trail below the post-earnings 10DMA or prior swing low.":
    "到达目标 1 后，止损跟随业绩后的 10 日均线或前一波段低点。",
  "Review if no follow-through within 20 trading days.":
    "若 20 个交易日内没有延续走势，需要复核。",
  "Review if setup stalls for 20 trading days.": "若形态停滞 20 个交易日，需要复核。",
  "Review if there is no drift follow-through within 20 trading days after earnings.":
    "若业绩后 20 个交易日内没有漂移延续，需要复核。",
  "Position size should be based on stop distance and portfolio risk budget.":
    "仓位大小应基于止损距离和组合风险预算。",
  "Watch trigger, invalidation, target, and data caveats before action.":
    "行动前观察触发价、失效条件、目标位和数据限制。",
  "Position is at or below the stored invalidation level.":
    "持仓已到达或跌破记录的失效位。",
  "Position has reached the first stored target.": "持仓已到达记录的第一目标位。",
  "Position is close to the stored stop.": "持仓接近记录的止损位。",
  "Position is close to the first stored target.": "持仓接近第一目标位。",
  "No strategy backtest validation is available for this brief.":
    "当前简报没有可用的策略回测验证。",
  "No catalyst hypotheses returned for this brief.": "当前简报没有返回催化假设。",
  "Check each trigger against volume, invalidation, and no-chase levels.":
    "逐个检查触发价、成交量、失效位和不追高价位。",
  "Validate catalyst transmission through orders, guidance, estimates, or margins.":
    "通过订单、指引、预期或利润率验证催化是否传导到财务。",
  "Review positions near stop or target levels before adding exposure.":
    "加仓前先复核接近止损或目标位的持仓。",
  "Resolve material data caveats before treating any setup as actionable.":
    "在把机会视为可执行前，先解决关键数据限制。",
  "Treat the brief as research context, not personalized investment advice.":
    "把简报当作研究上下文，而不是个性化投资建议。",
  "Deterministic development data for US:TEST and CN:000001.":
    "用于本地开发的确定性样例数据。",
  "Free market data; may be delayed or incomplete.": "免费行情数据，可能延迟或不完整。",
  "Free A-share market data with provider-dependent coverage.":
    "免费 A股行情数据，覆盖范围取决于上游数据源。",
  "Used for company overview, earnings history, and current analyst ratings.":
    "用于公司概览、历史业绩和当前分析师评级。",
  "Needed for true analyst revision scoring when estimate history is available.":
    "在可获取预期历史时，用于更真实的分析师上修评分。",
  "Adds earnings calendar, basic financials, and recommendation trends.":
    "补充业绩日历、基础财务和推荐趋势。",
  "Requires a clear SEC user agent; current config supplies one.":
    "需要明确的 SEC User-Agent；当前配置已提供。",
  "Free A-share announcements; live access can be rate-limited.":
    "免费 A股公告数据；实时访问可能受限流影响。",
  "Configured by token, but deeper normalized adapters are still provider-dependent.":
    "通过 token 配置；更深层的标准化适配仍依赖数据源能力。",
  "Trigger when price confirms the opportunity entry level.":
    "当价格确认机会入场位时触发。",
  "Warn when price invalidates the stored trade plan.": "当价格触及交易计划失效位时提醒。",
  "Warn when price reaches the first planned target.": "当价格到达第一目标位时提醒。",
  "News may indicate incremental demand. Map it to orders, backlog, revenue, and gross margin before treating it as investable.":
    "新闻可能意味着新增需求。需要先映射到订单、在手订单、收入和毛利率，再判断是否具备投资价值。",
  "Check follow-up orders, backlog commentary, revenue line items, and margin trend.":
    "检查后续订单、在手订单表述、收入科目和利润率趋势。",
  "News may imply earnings revision. Validate whether consensus estimates and company guidance actually move.":
    "新闻可能意味着业绩预期上修。需要验证一致预期和公司指引是否真的变化。",
  "Check estimate revisions, management guidance, and next-quarter revenue growth.":
    "检查预期上修、管理层指引和下一季度收入增长。",
  "News may affect shareholder return expectations, but usually needs earnings support to sustain a rerating.":
    "新闻可能影响股东回报预期，但通常需要业绩支撑才能维持估值重估。",
  "Check authorization size, execution pace, cash flow, and valuation reaction.":
    "检查授权规模、执行节奏、现金流和估值反应。",
  "News is relevant context, but the financial transmission path is not obvious yet.":
    "新闻具有相关性，但财务传导路径暂时还不明确。",
  "Identify affected revenue item, timing, margin impact, and whether estimates change.":
    "识别受影响的收入科目、发生时间、利润率影响，以及预期是否变化。",
  "Entry triggered by daily high crossing trigger.": "日内高点突破触发价，入场触发。",
  "Entry trigger expired before execution.": "入场触发在执行前过期。",
  "Initial stop touched.": "触及初始止损。",
  "Target 1 touched.": "触及目标 1。",
  "Maximum holding window reached.": "达到最大持有窗口。",
  "Trend weakens if price loses the rising 50DMA or the signal stack reverses.":
    "若价格跌破上行的 50 日均线，或信号栈反转，趋势转弱。",
  "Breakout fails if price closes back below pivot/support on weak follow-through.":
    "若价格收回枢轴/支撑下方且后续无力，突破失败。",
  "Pullback fails if price loses support or closes below the rising 50DMA.":
    "若价格跌破支撑或收盘跌破上行 50 日均线，回调形态失败。",
  "Health deteriorates if moving averages flatten or price becomes unsupported.":
    "若均线走平或价格失去支撑，趋势健康度恶化。",
  "Catalyst weakens if no order, revenue, margin, or guidance verification appears.":
    "若没有订单、收入、利润率或指引验证，催化减弱。",
  "Revision momentum fails if forward estimates flatten or reverse.":
    "若未来预期走平或转为下修，上修动量失效。",
  "TAM-adjusted valuation weakens if growth duration or margin conversion decays.":
    "若增长持续期或利润转化变弱，TAM 调整估值逻辑减弱。",
  "Bayesian valuation weakens if new evidence lowers durable growth odds.":
    "若新证据降低长期增长概率，贝叶斯估值逻辑减弱。",
  "Regime support fades if sector breadth and benchmark trend roll over.":
    "若行业广度和基准趋势转弱，市场环境支撑减弱。",
  "Squeeze risk fades when volume normalizes and price loses the trigger level.":
    "若成交量恢复正常且价格跌破触发位，逼空风险减弱。",
  "Options confirmation fails if flow is identified as hedge, spread, or IV-only.":
    "若期权流被识别为对冲、价差或仅 IV 交易，确认失效。",
  "Ownership confirmation weakens when purchases stop or filings show distribution.":
    "若买入停止或文件显示减持，持有人确认减弱。",
  "trend_strength signal absent": "缺少趋势强度信号",
  "breakout signal absent": "缺少突破信号",
  "trend exists but pullback has not formed": "趋势存在，但回调形态尚未形成",
  "trend and pullback signals absent": "缺少趋势和回调信号",
  "moving-average trend absent": "缺少均线趋势",
  "earnings actuals, estimates, or announcement timing unavailable":
    "实际业绩、业绩预期或公告时间不可用",
  "earnings date is not present in the price history": "价格历史中缺少业绩公告日",
  "analyst estimate or revision timestamp unavailable": "分析师预期或上修时间不可用",
  "fundamental growth, valuation, or TAM proxy unavailable":
    "基本面增长、估值或 TAM 代理变量不可用",
  "fundamental growth, valuation, or growth prior unavailable":
    "基本面增长、估值或成长先验不可用",
  "SEC ownership filings unavailable": "SEC 持有人文件不可用",
  "ownership filings do not match insider or institutional forms":
    "持有人文件不匹配内部人或机构持仓表格",
  "required data is not available in the current free-data scan":
    "当前免费数据扫描中缺少所需数据",
};

export function localizeAction(value: string | null | undefined, language: Language): string {
  return label(ACTION_LABELS, value, language);
}

export function localizeProfile(value: string | null | undefined, language: Language): string {
  return label(PROFILE_LABELS, value, language);
}

export function localizeProfileReason(value: string | null | undefined, language: Language): string {
  return label(PROFILE_REASON_LABELS, value, language);
}

export function localizeStatus(value: string | null | undefined, language: Language): string {
  return label(STATUS_LABELS, value, language);
}

export function localizeRiskStatus(value: string | null | undefined, language: Language): string {
  return label(STATUS_LABELS, value, language);
}

export function localizeRiskVeto(value: string | null | undefined, language: Language): string {
  return label(RISK_VETO_LABELS, value, language);
}

export function localizeRiskVetoMessage(
  value: string | null | undefined,
  fallback: string | null | undefined,
  language: Language,
): string {
  const localized = label(RISK_VETO_MESSAGES, value, language);
  return localized === humanize(value ?? "") ? localizeReason(fallback, language) : localized;
}

export function localizeScanBlocker(value: string | null | undefined, language: Language): string {
  return label(SCAN_BLOCKER_LABELS, value, language);
}

export function localizeScanBlockerMessage(
  value: string | null | undefined,
  fallback: string | null | undefined,
  language: Language,
): string {
  const localized = label(SCAN_BLOCKER_MESSAGES, value, language);
  return localized === humanize(value ?? "") ? localizeReason(fallback, language) : localized;
}

export function localizeRadarSignal(value: string | null | undefined, language: Language): string {
  return label(RADAR_SIGNAL_LABELS, value, language);
}

export function localizeRadarAction(value: string | null | undefined, language: Language): string {
  return label(RADAR_ACTION_LABELS, value, language);
}

export function localizeDiagnosticVerdict(
  value: string | null | undefined,
  language: Language,
): string {
  return label(DIAGNOSTIC_VERDICT_LABELS, value, language);
}

export function localizeDiagnosticReason(
  value: string | null | undefined,
  fallback: string | null | undefined,
  language: Language,
): string {
  const localized = label(DIAGNOSTIC_REASON_LABELS, value, language);
  return localized === humanize(value ?? "") ? localizeReason(fallback, language) : localized;
}

export function localizeStrategy(value: string | null | undefined, language: Language): string {
  if (!value) {
    return "-";
  }
  const id = STRATEGY_NAME_TO_ID[value] ?? value;
  return label(STRATEGY_LABELS, id, language);
}

export function localizeStrategyFamily(value: string | null | undefined, language: Language): string {
  return label(FAMILY_LABELS, value, language);
}

export function localizeRole(value: string | null | undefined, language: Language): string {
  return label(ROLE_LABELS, value, language);
}

export function localizeSignal(value: string | null | undefined, language: Language): string {
  return label(SIGNAL_LABELS, value, language);
}

export function localizeDirection(value: string | null | undefined, language: Language): string {
  return label(DIRECTION_LABELS, value, language);
}

export function localizeFactor(value: string | null | undefined, language: Language): string {
  return label(FACTOR_LABELS, value, language);
}

export function localizeFactorExplanation(
  factorId: string | null | undefined,
  fallback: string,
  language: Language,
): string {
  if (language === "zh" && factorId && FACTOR_EXPLANATIONS_ZH[factorId]) {
    return FACTOR_EXPLANATIONS_ZH[factorId];
  }
  return fallback;
}

export function localizeFactorFlag(value: string | null | undefined, language: Language): string {
  return label(FACTOR_FLAGS, value, language);
}

export function localizeAlertKind(value: string | null | undefined, language: Language): string {
  return label(ALERT_KIND_LABELS, value, language);
}

export function localizeCatalyst(value: string | null | undefined, language: Language): string {
  return label(CATALYST_LABELS, value, language);
}

export function localizeDataRequirement(
  value: string | null | undefined,
  language: Language,
): string {
  return label(DATA_REQUIREMENT_LABELS, value, language);
}

export function localizeDataHealthKey(value: string, language: Language): string {
  return label(DATA_HEALTH_KEYS, value, language);
}

export function localizeDataHealthValue(value: string, language: Language): string {
  return label(DATA_HEALTH_VALUES, value, language);
}

export function localizeProvider(value: string | null | undefined, language: Language): string {
  if (!value) {
    return "-";
  }
  return label(DATA_HEALTH_VALUES, value, language);
}

export function localizeProviderName(value: string | null | undefined, language: Language): string {
  return label(PROVIDER_NAME_LABELS, value, language);
}

export function localizeCapability(value: string | null | undefined, language: Language): string {
  return label(CAPABILITY_LABELS, value, language);
}

export function localizeCaveat(value: string | null | undefined, language: Language): string {
  if (!value) {
    return "-";
  }
  if (language !== "zh") {
    return value;
  }
  if (value === "fixture data") {
    return "样例数据";
  }
  if (value.startsWith("provider: ")) {
    return `数据源：${value.replace("provider: ", "")}`;
  }
  return localizeFactorFlag(value, language);
}

export function localizeReason(value: string | null | undefined, language: Language): string {
  if (!value) {
    return "-";
  }
  if (language !== "zh") {
    return value;
  }
  const text = value.trim();
  if (EXACT_TEXT_ZH[text]) {
    return EXACT_TEXT_ZH[text];
  }
  if (text.startsWith("provider: ") || text === "fixture data") {
    return localizeCaveat(text, language);
  }

  let match = text.match(/^Primary strategy is (.+?): (.+)\. Review entry, invalidation, and missing-data caveats before action\.$/);
  if (match) {
    const strategy = localizeStrategy(match[1], language);
    const triggers =
      match[2] === "setup forming"
        ? "形态形成中"
        : match[2]
            .split(",")
            .map((item) => localizeSignal(item.trim(), language))
            .join("、");
    return `主策略是${strategy}：${triggers}。行动前请检查入场、失效条件和缺失数据。`;
  }

  match = text.match(/^(.+) is the primary strategy$/);
  if (match) {
    return `主策略：${localizeStrategy(match[1], language)}`;
  }

  match = text.match(/^Primary strategy: (.+)\.$/);
  if (match) {
    return `主策略：${localizeStrategy(match[1], language)}。`;
  }

  match = text.match(/^Risk\/reward is ([\d.-]+)\.?$/);
  if (match) {
    return `盈亏比 ${match[1]}。`;
  }

  match = text.match(/^(\d+) registered strategies still need data$/);
  if (match) {
    return `${match[1]} 个已注册策略仍缺少数据`;
  }

  match = text.match(/^(\d+) strategies are passed or watch$/);
  if (match) {
    return `${match[1]} 个策略处于通过或观察状态`;
  }

  match = text.match(/^factor flag: (.+)$/);
  if (match) {
    return `因子标签：${localizeFactorFlag(match[1], language)}`;
  }

  match = text.match(
    /^Conviction score is ([\d.]+) from strategy, risk\/reward, data quality, and execution quality\.$/,
  );
  if (match) {
    return `信心分 ${match[1]}，由策略质量、盈亏比、数据质量和执行质量共同决定。`;
  }

  match = text.match(/^Invalid if price trades at or below stop ([\d.]+)\.$/);
  if (match) {
    return `若价格跌到或低于止损位 ${match[1]}，机会失效。`;
  }

  match = text.match(/^Do not chase above ([\d.]+) without a fresh setup\.$/);
  if (match) {
    return `没有新形态确认前，不要追高到 ${match[1]} 以上。`;
  }

  match = text.match(/^Confirm price respects trigger ([\d.]+)\.$/);
  if (match) {
    return `确认价格能有效守住/尊重触发价 ${match[1]}。`;
  }

  match = text.match(/^Recheck evidence for (.+)\.$/);
  if (match) {
    return `重新检查${localizeStrategy(match[1], language)}的证据。`;
  }

  match = text.match(/^Resolve missing data before sizing up: (.+)\.$/);
  if (match) {
    return `加大仓位前先补齐缺失数据：${localizeCsv(match[1], language)}。`;
  }

  match = text.match(/^Review data caveats: (.+)\.$/);
  if (match) {
    return `复核数据限制：${match[1]
      .split(";")
      .map((item) => localizeCaveat(item.trim(), language))
      .join("；")}。`;
  }

  match = text.match(/^At trigger, risk to stop is ([\d.-]+)%; target 1 is \+([\d.-]+)%\.$/);
  if (match) {
    return `按触发价入场，止损风险为 ${match[1]}%；第一目标收益为 +${match[2]}%。`;
  }

  match = text.match(/^Optional data providers missing config: (.+)\.$/);
  if (match) {
    return `可选数据源缺少配置：${match[1]}。`;
  }

  match = text.match(/^(.+) triggered at (.+)$/);
  if (match) {
    return `${localizeAlertKind(match[1], language)}已在 ${match[2]} 触发`;
  }

  match = text.match(
    /^(\d+) setup-ready opportunities; (\d+) strateg(?:y|ies) with validation samples; (\d+) position risk alerts\.$/,
  );
  if (match) {
    return `${match[1]} 个准备机会；${match[2]} 个策略有验证样本；${match[3]} 条持仓风险提醒。`;
  }

  match = text.match(/^Qagent Alerts: (\d+) triggered$/);
  if (match) {
    return `Qagent 提醒：${match[1]} 条触发`;
  }

  return text;
}

export function localizeEvidenceKey(value: string, language: Language): string {
  const signalLabel = label(SIGNAL_LABELS, value, language);
  if (signalLabel !== humanize(value)) {
    return signalLabel;
  }
  return label(EVIDENCE_KEYS, value, language);
}

export function localizeEvidenceValue(value: unknown, language: Language): string {
  if (Array.isArray(value)) {
    return value.map((item) => localizeEvidenceValue(item, language)).join("、");
  }
  if (typeof value === "string") {
    return localizeReason(value, language);
  }
  return String(value);
}

export function localizeList(
  items: string[],
  language: Language,
  localizer: (value: string, language: Language) => string = localizeReason,
): string {
  if (!items.length) {
    return "-";
  }
  return items.map((item) => localizer(item, language)).join(language === "zh" ? "、" : ", ");
}

function localizeCsv(value: string, language: Language): string {
  return value
    .split(",")
    .map((item) => localizeDataRequirement(item.trim(), language))
    .join("、");
}

function label(map: LabelMap, value: string | null | undefined, language: Language): string {
  if (!value) {
    return "-";
  }
  const item = map[value];
  if (item) {
    return item[language];
  }
  return humanize(value);
}

function humanize(value: string): string {
  return value.replace(/_/g, " ");
}

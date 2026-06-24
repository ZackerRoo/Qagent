from qagent.agent.responder import answer_question


def test_responder_refuses_to_guarantee_returns():
    answer = answer_question("Will US:TEST definitely go up?", context={})
    assert "cannot guarantee" in answer.lower()


def test_responder_answers_from_context():
    answer = answer_question(
        "Where is the stop?",
        context={"instrument_id": "US:TEST", "initial_stop": "98.00", "status": "setup_ready"},
    )
    assert "98.00" in answer


def test_responder_explains_buy_scenario_from_structured_context():
    answer = answer_question(
        "If I buy this, what happens?",
        context={
            "instrument_id": "US:TEST",
            "status": "setup_ready",
            "trigger_price": "102.00",
            "initial_stop": "98.00",
            "target_1": "110.00",
            "downside_pct": -3.92,
            "target_1_pct": 7.84,
            "no_chase_above": "106.00",
        },
    )

    assert "102.00" in answer
    assert "98.00" in answer
    assert "110.00" in answer
    assert "-3.92%" in answer
    assert "+7.84%" in answer
    assert "not advice" in answer.lower()


def test_responder_explains_why_from_signal_summary():
    answer = answer_question(
        "Why is this on the list?",
        context={
            "instrument_id": "US:TEST",
            "status": "setup_ready",
            "score": 0.91,
            "signal_summary": "trend_strength bullish 0.75; breakout bullish 0.66",
        },
    )

    assert "trend_strength" in answer
    assert "breakout" in answer
    assert "0.91" in answer


def test_responder_recommends_ranked_cards_with_entry_and_exit_levels():
    answer = answer_question(
        "今天推荐什么股票，什么时候买什么时候卖？",
        context={
            "cards": [
                {
                    "instrument_id": "CN:000063",
                    "action": "watch_trigger",
                    "conviction_score": 0.63,
                    "trigger_price": "38.01",
                    "initial_stop": "35.82",
                    "target_1": "41.05",
                    "target_2": "42.57",
                    "no_chase_above": "39.53",
                    "risk_reward": 1.39,
                    "factor_score": 0.82,
                    "factor_rank": 1,
                    "factor_flags": ["overextended"],
                    "primary_strategy_id": "healthy_pullback",
                    "data_caveats": ["provider: baostock"],
                },
                {
                    "instrument_id": "CN:600519",
                    "action": "avoid",
                    "conviction_score": 0.41,
                    "trigger_price": None,
                    "initial_stop": None,
                    "target_1": None,
                    "target_2": None,
                    "no_chase_above": None,
                    "risk_reward": None,
                    "factor_score": 0.21,
                    "factor_rank": 2,
                    "factor_flags": [],
                    "primary_strategy_id": None,
                    "data_caveats": ["provider: baostock"],
                },
            ],
            "provider": "free",
        },
    )

    assert "中兴通讯 000063.SZ" in answer
    assert "CN:000063" not in answer
    assert "38.01" in answer
    assert "35.82" in answer
    assert "41.05" in answer
    assert "39.53" in answer
    assert "因子" in answer
    assert "0.82" in answer
    assert "overextended" in answer
    assert "baostock" in answer
    assert "不是投资建议" in answer

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

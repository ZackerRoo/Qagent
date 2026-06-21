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

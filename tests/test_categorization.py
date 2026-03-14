from app.jobs.pipeline import classify_event


def test_classify_event_ai_models() -> None:
    label, confidence = classify_event(
        "OpenAI announces new GPT model release",
        "The latest LLM launch improves coding quality.",
        "general",
    )
    assert label == "ai-models"
    assert confidence >= 0.8


def test_classify_event_fallback() -> None:
    label, confidence = classify_event(
        "Community meetup this weekend",
        "Local meetup for developers and students.",
        "general",
    )
    assert label == "general"
    assert confidence == 0.55

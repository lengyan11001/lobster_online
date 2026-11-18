from backend.app.api.multi_clip_mixer import _score_sentences


def test_caption_microseconds_are_converted_to_seconds():
    """STT captions start/end are microseconds; report/trim must use seconds."""
    scored = _score_sentences([
        {"text": "\u8fd9\u662f\u4e00\u4e2a\u6d4b\u8bd5\u53e5\u5b50", "start": 0, "end": 26_760_000},
    ])
    assert len(scored) == 1
    assert scored[0]["start"] == 0
    assert scored[0]["end"] == 26.76

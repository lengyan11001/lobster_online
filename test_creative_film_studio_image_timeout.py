from backend.app.api import creative_film_studio as module


def test_image_generation_timeout_matches_server_mcp_window() -> None:
    assert module._SUTUI_IMAGE_SUBMIT_TIMEOUT_SECONDS == 25 * 60.0

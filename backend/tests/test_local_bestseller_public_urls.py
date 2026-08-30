from types import SimpleNamespace

from starlette.requests import Request

from backend.app.api import local_bestseller


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/local-bestseller/scene/generate",
            "headers": [],
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
        }
    )


def test_local_bestseller_resolves_asset_id_instead_of_preview_url(monkeypatch):
    monkeypatch.setattr(
        local_bestseller,
        "get_asset_public_url",
        lambda *_args, **_kwargs: "https://lobster-online-assets.tos-cn-guangzhou.volces.com/assets/photo.jpg",
    )

    result = local_bestseller._resolve_reference_urls(
        profile={
            "photo_url": "http://127.0.0.1:8000/api/assets/file/local-photo?token=abc",
            "photo_asset_id": "photo-1",
        },
        card={},
        current_user=SimpleNamespace(id=1),
        request=_request(),
        db=None,
    )

    assert result == [
        "https://lobster-online-assets.tos-cn-guangzhou.volces.com/assets/photo.jpg"
    ]


def test_local_bestseller_resolves_public_asset_for_video_when_card_url_is_local(monkeypatch):
    monkeypatch.setattr(
        local_bestseller,
        "get_asset_public_url",
        lambda *_args, **_kwargs: "https://lobster-online-assets.tos-cn-guangzhou.volces.com/assets/scene.jpg",
    )

    result = local_bestseller._resolve_card_image_url(
        card={
            "image_url": "http://127.0.0.1:8000/api/assets/file/local-scene?token=abc",
            "image_asset_id": "scene-1",
        },
        current_user=SimpleNamespace(id=1),
        request=_request(),
        db=None,
    )

    assert result == "https://lobster-online-assets.tos-cn-guangzhou.volces.com/assets/scene.jpg"

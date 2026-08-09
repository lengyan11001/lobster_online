import asyncio
from pathlib import Path

from backend.app.api import h5_chat_channel


ROOT = Path(__file__).resolve().parent


def test_online_content_and_ip_cards_expose_copy_images_and_moments_publish():
    publish = (ROOT / "static" / "js" / "publish.js").read_text(encoding="utf-8")
    ip_studio = (ROOT / "static" / "js" / "ip-content-studio.js").read_text(encoding="utf-8")
    wechat = (ROOT / "static" / "js" / "juhe-wechat.js").read_text(encoding="utf-8")

    assert "function _contentRecordImageAssetIds(item)" in publish
    assert "function _contentRecordImageRefs(item)" in publish
    assert "function _contentRecordDisplayLabel(item)" in publish
    assert "industry_hot_oral: '行业口播文案'" in publish
    assert "professional_ip_oral: 'IP口播文案'" in publish
    assert "add(item.image_url);" in publish
    assert "walk(item.image_results, true);" in publish
    assert "image_asset_ids: imageAssetIds" in publish
    assert "function _isMomentsContentRecord(asset)" in publish
    assert "add('publish_moments', '发布到朋友圈')" in publish
    assert "function _assetOpenMomentsPublish(asset)" in publish
    assert "class=\"asset-content-image-strip\"" in publish
    assert "window.prefillNativeWechatMoments" in wechat
    assert "publish_draft: {" in wechat
    assert "images: remoteRefs.slice(0, 9)" in wechat
    assert "state.momentsContentRecord" in wechat
    assert "cloudJson('/api/content-records/publish-request'" in wechat
    assert "data-record-action=\"publish-moments\"" in ip_studio
    assert "data-publish-moment-image-record" in ip_studio
    assert "addList(imageUpdate.image_results);" in ip_studio


def test_publish_draft_resolves_all_image_urls_and_asset_ids(monkeypatch):
    downloaded = []

    async def fake_download(url, **_kwargs):
        downloaded.append(url)
        return {"source_url": url, "kind": "image", "local_path": url}

    monkeypatch.setattr(h5_chat_channel, "_download_url_to_native_wechat_attachment", fake_download)
    monkeypatch.setattr(
        h5_chat_channel,
        "_local_asset_to_native_wechat_attachment",
        lambda asset_id: {"asset_id": asset_id, "kind": "image", "local_path": asset_id},
    )

    result = asyncio.run(
        h5_chat_channel._wechat_moments_attachments_from_draft(
            {
                "image_urls": [
                    "https://cdn.example.test/one.jpg",
                    "https://cdn.example.test/two.jpg",
                ],
                "image_asset_ids": ["", "", "asset-three"],
            },
            {},
        )
    )

    assert [item.get("asset_id") for item in result if item.get("asset_id")] == ["asset-three"]
    assert downloaded == [
        "https://cdn.example.test/one.jpg",
        "https://cdn.example.test/two.jpg",
    ]
    assert len(result) == 3

import pytest

from src.notify import tmpfiles_image


PNG = tmpfiles_image.PNG_SIGNATURE + b"test-image"


class _Response:
    def __init__(self, *, payload=None, text="", content=b"", headers=None):
        self._payload = payload
        self.text = text
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_upload_png_to_tmpfiles_returns_verified_direct_image_url(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append(("post", url, kwargs["data"]))
        return _Response(payload={
            "status": "success",
            "data": {"url": "https://tmpfiles.org/abc/chart.png"},
        })

    def fake_get(url, **kwargs):
        calls.append(("get", url, kwargs))
        if url == "https://tmpfiles.org/abc/chart.png":
            return _Response(text=(
                '<a href="https://tmpfiles.org/dl/123.hash/abc/chart.png">Download</a>'
            ))
        return _Response(content=PNG, headers={"Content-Type": "image/png"})

    monkeypatch.setattr(tmpfiles_image.requests, "post", fake_post)
    monkeypatch.setattr(tmpfiles_image.requests, "get", fake_get)

    direct_url = tmpfiles_image.upload_png_to_tmpfiles(PNG, "chart.png")

    assert direct_url == "https://tmpfiles.org/dl/123.hash/abc/chart.png"
    assert calls[0][2]["expire"] == str(tmpfiles_image.DEFAULT_EXPIRY_SECONDS)
    assert calls[1][1] == "https://tmpfiles.org/abc/chart.png"
    assert calls[2][1] == direct_url


@pytest.mark.parametrize("image", [b"", b"not-a-png"])
def test_upload_png_to_tmpfiles_rejects_non_png_without_network(monkeypatch, image):
    monkeypatch.setattr(
        tmpfiles_image.requests,
        "post",
        lambda *args, **kwargs: pytest.fail("Should reject image before upload"),
    )

    with pytest.raises(ValueError, match="PNG"):
        tmpfiles_image.upload_png_to_tmpfiles(image, "chart.png")


def test_upload_png_to_tmpfiles_rejects_non_image_download(monkeypatch):
    monkeypatch.setattr(
        tmpfiles_image.requests,
        "post",
        lambda *args, **kwargs: _Response(payload={
            "status": "success",
            "data": {"url": "https://tmpfiles.org/abc/chart.png"},
        }),
    )
    monkeypatch.setattr(
        tmpfiles_image.requests,
        "get",
        lambda url, **kwargs: (
            _Response(text='<a href="/dl/123.hash/abc/chart.png">Download</a>')
            if url == "https://tmpfiles.org/abc/chart.png"
            else _Response(content=b"<html>", headers={"Content-Type": "text/html"})
        ),
    )

    with pytest.raises(RuntimeError, match="image/png"):
        tmpfiles_image.upload_png_to_tmpfiles(PNG, "chart.png")


def test_upload_png_to_tmpfiles_rejects_invalid_expiry():
    with pytest.raises(ValueError, match="有效時間"):
        tmpfiles_image.upload_png_to_tmpfiles(PNG, "chart.png", expire_seconds=10)

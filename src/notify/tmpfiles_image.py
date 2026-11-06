"""Upload temporary notification images to tmpfiles.org."""

from __future__ import annotations

from html.parser import HTMLParser
from io import BytesIO
from urllib.parse import urljoin, urlparse

import requests

TMPFILES_UPLOAD_URL = "https://tmpfiles.org/api/v1/upload"
DEFAULT_EXPIRY_SECONDS = 6 * 60 * 60
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class _DownloadLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.urls.append(href)


def upload_png_to_tmpfiles(
    image: bytes,
    filename: str,
    expire_seconds: int = DEFAULT_EXPIRY_SECONDS,
) -> str:
    """Upload a PNG and return a verified direct HTTPS image URL."""
    if not image.startswith(PNG_SIGNATURE):
        raise ValueError("上傳 tmpfiles 的圖表不是有效的 PNG。")
    if not 60 <= expire_seconds <= 172800:
        raise ValueError("tmpfiles 圖片有效時間需介於 60 秒至 172800 秒。")

    response = requests.post(
        TMPFILES_UPLOAD_URL,
        files={"file": (filename, BytesIO(image), "image/png")},
        data={"expire": str(expire_seconds)},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    upload_url = payload.get("data", {}).get("url")
    if payload.get("status") != "success" or not isinstance(upload_url, str):
        raise RuntimeError("tmpfiles 未回報圖片上傳成功。")

    parsed_upload_url = urlparse(upload_url)
    if parsed_upload_url.scheme != "https" or parsed_upload_url.hostname != "tmpfiles.org":
        raise RuntimeError("tmpfiles 回傳了不受信任的圖片頁面網址。")

    page_response = requests.get(upload_url, timeout=20)
    page_response.raise_for_status()
    parser = _DownloadLinkParser()
    parser.feed(page_response.text)
    direct_url = next(
        (
            urljoin(upload_url, href)
            for href in parser.urls
            if urlparse(urljoin(upload_url, href)).scheme == "https"
            and urlparse(urljoin(upload_url, href)).hostname == "tmpfiles.org"
            and urlparse(urljoin(upload_url, href)).path.startswith("/dl/")
        ),
        None,
    )
    if direct_url is None:
        raise RuntimeError("無法從 tmpfiles 頁面取得圖片直連網址。")

    image_response = requests.get(direct_url, timeout=30)
    image_response.raise_for_status()
    if not image_response.headers.get("Content-Type", "").lower().startswith("image/png"):
        raise RuntimeError("tmpfiles 圖片直連未回傳 image/png。")
    if not image_response.content.startswith(PNG_SIGNATURE):
        raise RuntimeError("tmpfiles 圖片直連內容不是有效的 PNG。")
    return direct_url

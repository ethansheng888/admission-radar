from __future__ import annotations

import re
from collections.abc import Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import RequestConfig, WebsiteConfig
from .models import Notice


class FetchError(RuntimeError):
    """抓取或解析公告页失败。"""


Parser = Callable[[bytes, str], list[Notice]]


def canonicalize_url(url: str) -> str:
    """去掉片段并统一主机名大小写，生成稳定的公告标识。"""

    parts = urlsplit(url)
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path,
            parts.query,
            "",
        )
    )


def _parse_cufe_date(raw_text: str) -> str | None:
    # 页面格式为“07-17 2026”，转换成更适合邮件和数据库的 ISO 日期。
    match = re.search(r"(\d{2})-(\d{2})\s+(\d{4})", raw_text)
    if not match:
        return None
    month, day, year = match.groups()
    return f"{year}-{month}-{day}"


def parse_cufe_master(html: bytes, page_url: str) -> list[Notice]:
    """解析中央财经大学研究生院“硕士招生（双证）”列表页。"""

    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.select("div.inner_s1 ul > li > a[href]")

    notices: list[Notice] = []
    seen_urls: set[str] = set()
    for anchor in anchors:
        href = str(anchor.get("href", "")).strip()
        title = str(anchor.get("title", "")).strip()
        if not title:
            title_node = anchor.select_one("h3")
            title = (
                title_node.get_text(" ", strip=True)
                if title_node
                else anchor.get_text(" ", strip=True)
            )
        title = " ".join(title.split())
        if not href or not title:
            continue

        absolute_url = canonicalize_url(urljoin(page_url, href))
        parsed = urlsplit(absolute_url)
        # 仅接受研究生院正文链接，避免页面结构变化时误抓导航和分页。
        if parsed.netloc.lower() != urlsplit(page_url).netloc.lower():
            continue
        if not re.fullmatch(r"/info/1028/\d+\.htm", parsed.path):
            continue
        if absolute_url in seen_urls:
            continue

        time_node = anchor.select_one("time")
        published_date = (
            _parse_cufe_date(time_node.get_text(" ", strip=True))
            if time_node
            else None
        )
        notices.append(
            Notice(
                title=title,
                url=absolute_url,
                published_date=published_date,
            )
        )
        seen_urls.add(absolute_url)

    if not notices:
        raise FetchError(
            "未在中财硕士招生页面提取到公告。"
            "网站结构可能已变化，程序已停止本次更新以避免误判。"
        )
    return notices


PARSERS: dict[str, Parser] = {
    "cufe_master": parse_cufe_master,
}


def build_session(config: RequestConfig) -> requests.Session:
    retry = Retry(
        total=config.retries,
        connect=config.retries,
        read=config.retries,
        status=config.retries,
        backoff_factor=config.retry_backoff_seconds,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": config.user_agent,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def fetch_notices(
    session: requests.Session,
    website: WebsiteConfig,
    request_config: RequestConfig,
) -> list[Notice]:
    parser = PARSERS.get(website.parser)
    if parser is None:
        available = "、".join(sorted(PARSERS))
        raise FetchError(
            f"未知解析器“{website.parser}”；当前可用解析器：{available}"
        )

    try:
        response = session.get(
            website.url,
            timeout=request_config.timeout_seconds,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise FetchError(f"访问公告页失败：{exc}") from exc

    content_type = response.headers.get("Content-Type", "")
    if "html" not in content_type.lower() and not response.content.lstrip().startswith(
        b"<!DOCTYPE"
    ):
        raise FetchError(f"公告页返回的不是 HTML：{content_type or '未知类型'}")

    return parser(response.content, website.url)

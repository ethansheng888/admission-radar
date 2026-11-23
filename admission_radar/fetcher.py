from __future__ import annotations

import logging
import re
import ssl
from collections.abc import Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import certifi

from .config import RequestConfig, WebsiteConfig
from .models import Notice


class FetchError(RuntimeError):
    """抓取或解析公告页失败。"""


Parser = Callable[[bytes, str], list[Notice]]


def _decode_html(html: bytes) -> str:
    """Decode the monitored Chinese sites without charset guessing."""

    # Both monitored list pages are UTF-8. Passing raw bytes to
    # BeautifulSoup lets its optional detector guess the encoding, and a
    # dependency update can turn short Chinese fixtures (or pages) into
    # mojibake. Honour an optional UTF-8 BOM and fail safely otherwise.
    try:
        return html.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FetchError(
            "公告页不是有效的 UTF-8 内容，程序已停止本次更新以避免误判。"
        ) from exc


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

    soup = BeautifulSoup(_decode_html(html), "html.parser")
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


def parse_bjtu_master(html: bytes, page_url: str) -> list[Notice]:
    """解析北京交通大学研究生院“硕士招生”列表页。"""

    soup = BeautifulSoup(_decode_html(html), "html.parser")
    anchors = soup.select(
        "section.sub_right_list ul.list01 > li > a[href]"
    )

    notices: list[Notice] = []
    seen_urls: set[str] = set()
    page_host = urlsplit(page_url).netloc.lower()
    for anchor in anchors:
        href = str(anchor.get("href", "")).strip()
        title_node = anchor.select_one("p.timeListPartnerTitle")
        date_node = anchor.select_one("div.subListTime")
        title = (
            title_node.get_text(" ", strip=True)
            if title_node
            else ""
        )
        title = " ".join(title.split())
        if not href or not title:
            continue

        absolute_url = canonicalize_url(urljoin(page_url, href))
        parsed = urlsplit(absolute_url)
        if parsed.netloc.lower() != page_host:
            continue
        if not re.fullmatch(
            r"/sszs/[0-9a-fA-F]{32}\.htm",
            parsed.path,
        ):
            continue
        if absolute_url in seen_urls:
            continue

        published_date = None
        if date_node:
            date_text = date_node.get_text(" ", strip=True)
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text):
                published_date = date_text

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
            "未在北交硕士招生页面提取到公告。"
            "网站结构可能已变化，程序已停止本次更新以避免误判。"
        )
    return notices


PARSERS: dict[str, Parser] = {
    "bjtu_master": parse_bjtu_master,
    "cufe_master": parse_cufe_master,
}


class ScopedTLSAdapter(HTTPAdapter):
    def __init__(self, context: ssl.SSLContext, **kwargs):
        self.context = context
        super().__init__(**kwargs)

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = self.context
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, proxy, **kwargs):
        kwargs["ssl_context"] = self.context
        return super().proxy_manager_for(proxy, **kwargs)

    def build_connection_pool_key_attributes(self, request, verify, cert=None):
        if verify is False:
            raise FetchError("站点 TLS 配置不允许关闭证书校验。")
        host, options = super().build_connection_pool_key_attributes(request, verify, cert)
        options["ssl_context"] = self.context
        options["cert_reqs"] = "CERT_REQUIRED"
        return host, options


def build_session(config: RequestConfig, websites: tuple[WebsiteConfig, ...] = ()) -> requests.Session:
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
    for website in websites:
        if not website.allow_legacy_server_connect and website.tls_intermediate_path is None:
            continue
        parts = urlsplit(website.url)
        if website.allow_legacy_server_connect and parts.hostname != "gs.cufe.edu.cn":
            raise FetchError("旧 TLS 兼容仅允许央财 HTTPS 主机及已验证的中间证书。")
        if (
            parts.scheme != "https"
            or parts.hostname not in {"gs.cufe.edu.cn", "yzb.bjtu.edu.cn"}
            or parts.port not in (None, 443)
            or website.tls_intermediate_path is None
        ):
            raise FetchError("站点 TLS 配置仅允许已配置中间证书的中财或北交 HTTPS 443 主机。")
        context = ssl.create_default_context(cafile=certifi.where())
        context.load_verify_locations(cafile=str(website.tls_intermediate_path))
        # The supplied intermediate must still chain to a trusted root.
        context.verify_flags &= ~getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0)
        if website.allow_legacy_server_connect:
            # Python 3.10 does not expose SSL_OP_LEGACY_SERVER_CONNECT.
            context.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
            context.options |= ssl.OP_NO_RENEGOTIATION
        scoped_adapter = ScopedTLSAdapter(context, max_retries=retry)
        session.mount(f"https://{parts.hostname}/", scoped_adapter)
        session.mount(f"https://{parts.hostname}:443/", scoped_adapter)
    return session


def fetch_notices(
    session: requests.Session,
    website: WebsiteConfig,
    request_config: RequestConfig,
) -> list[Notice]:
    stage = "select_parser"
    response = None
    try:
        parser = PARSERS.get(website.parser)
        if parser is None:
            available = "、".join(sorted(PARSERS))
            raise FetchError(
                f"未知解析器“{website.parser}”；当前可用解析器：{available}"
            )

        stage = "http_request"
        try:
            response = session.get(
                website.url,
                timeout=request_config.timeout_seconds,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise FetchError("访问公告页失败。") from exc

        stage = "validate_response"
        if 300 <= response.status_code < 400:
            raise FetchError("公告页发生重定向，需要人工核查目标地址。")
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise FetchError("公告页返回 HTTP 错误。") from exc

        content_type = response.headers.get("Content-Type", "")
        if "html" not in content_type.lower() and not response.content.lstrip().startswith(
            b"<!DOCTYPE"
        ):
            raise FetchError("公告页返回的不是 HTML。")

        stage = "parse_announcements"
        return parser(response.content, website.url)
    except Exception as exc:
        # Log only exception class names and numeric status codes. Exception
        # messages can contain request details or be supplied by a remote peer.
        chain = []
        verify_code = None
        error = exc
        for _ in range(8):
            if error is None:
                break
            chain.append(type(error).__name__)
            if isinstance(error, ssl.SSLCertVerificationError):
                verify_code = getattr(error, "verify_code", None)
            error = error.__cause__ or error.__context__

        logging.getLogger(__name__).error(
            "site=%s stage=%s error=%s http_status=%s tls_verify_code=%s",
            website.id,
            stage,
            " > ".join(chain),
            response.status_code if response is not None else None,
            verify_code,
        )
        raise

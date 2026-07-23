from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class ConfigError(ValueError):
    """配置文件无效。"""


ENV_PLACEHOLDER = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


@dataclass(frozen=True)
class RequestConfig:
    timeout_seconds: int
    retries: int
    retry_backoff_seconds: float
    user_agent: str


@dataclass(frozen=True)
class EmailConfig:
    enabled: bool
    smtp_host: str
    smtp_port: int
    security: str
    username: str
    password: str
    password_env: str
    from_address: str
    to_addresses: tuple[str, ...]
    subject_prefix: str
    timeout_seconds: int

    def resolved_password(self) -> str:
        if self.password_env:
            env_value = os.environ.get(self.password_env, "").strip()
            if env_value:
                return env_value
        return self.password


@dataclass(frozen=True)
class WebsiteConfig:
    id: str
    name: str
    url: str
    parser: str


@dataclass(frozen=True)
class AppConfig:
    config_path: Path
    database_path: Path
    log_path: Path
    request: RequestConfig
    email: EmailConfig
    websites: tuple[WebsiteConfig, ...]


def _expect_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"“{field}”必须是 JSON 对象。")
    return value


def _resolve_environment_placeholders(value: Any, field: str = "配置") -> Any:
    """把完整的 ${NAME} 字符串替换为环境变量，避免把敏感信息写入仓库。"""

    if isinstance(value, str):
        match = ENV_PLACEHOLDER.fullmatch(value)
        if not match:
            return value
        variable_name = match.group(1)
        resolved = os.environ.get(variable_name)
        if resolved is None or not resolved.strip():
            raise ConfigError(
                f"{field}需要环境变量“{variable_name}”，但当前未设置。"
            )
        return resolved
    if isinstance(value, list):
        return [
            _resolve_environment_placeholders(item, f"{field}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        return {
            key: _resolve_environment_placeholders(item, f"{field}.{key}")
            for key, item in value.items()
        }
    return value


def _required_text(data: dict[str, Any], field: str, context: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{context}中的“{field}”不能为空。")
    return value.strip()


def _resolve_local_path(base_dir: Path, raw_path: Any, field: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ConfigError(f"“{field}”必须是非空路径。")
    path = Path(raw_path.strip())
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _validate_email(email: EmailConfig) -> None:
    if not email.enabled:
        return

    missing: list[str] = []
    if not email.smtp_host:
        missing.append("smtp_host")
    if not email.from_address:
        missing.append("from_address")
    if not email.to_addresses:
        missing.append("to_addresses")
    if email.username and not email.resolved_password():
        missing.append("password（或 password_env 对应的环境变量）")

    if missing:
        raise ConfigError("邮件已启用，但缺少：" + "、".join(missing))


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).expanduser().resolve()
    if not config_path.exists():
        raise ConfigError(
            f"找不到配置文件：{config_path}\n"
            "请先复制 config.example.json 为 config.json 并填写邮箱配置。"
        )

    try:
        with config_path.open("r", encoding="utf-8-sig") as handle:
            raw = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"配置文件不是有效 JSON：第 {exc.lineno} 行，第 {exc.colno} 列。"
        ) from exc
    except OSError as exc:
        raise ConfigError(f"无法读取配置文件：{exc}") from exc

    root = _expect_object(
        _resolve_environment_placeholders(raw),
        "根配置",
    )
    base_dir = config_path.parent

    request_raw = _expect_object(root.get("request", {}), "request")
    request = RequestConfig(
        timeout_seconds=int(request_raw.get("timeout_seconds", 20)),
        retries=int(request_raw.get("retries", 2)),
        retry_backoff_seconds=float(request_raw.get("retry_backoff_seconds", 1.0)),
        user_agent=str(
            request_raw.get(
                "user_agent",
                "AdmissionRadar/1.0 (+personal website change monitor)",
            )
        ).strip(),
    )
    if request.timeout_seconds <= 0:
        raise ConfigError("request.timeout_seconds 必须大于 0。")
    if request.retries < 0:
        raise ConfigError("request.retries 不能小于 0。")

    email_raw = _expect_object(root.get("email", {}), "email")
    recipients_raw = email_raw.get("to_addresses", [])
    if not isinstance(recipients_raw, list) or not all(
        isinstance(item, str) for item in recipients_raw
    ):
        raise ConfigError("email.to_addresses 必须是字符串数组。")

    security = str(email_raw.get("security", "ssl")).strip().lower()
    if security not in {"ssl", "starttls", "none"}:
        raise ConfigError("email.security 只能是 ssl、starttls 或 none。")

    email = EmailConfig(
        enabled=bool(email_raw.get("enabled", False)),
        smtp_host=str(email_raw.get("smtp_host", "")).strip(),
        smtp_port=int(email_raw.get("smtp_port", 465)),
        security=security,
        username=str(email_raw.get("username", "")).strip(),
        password=str(email_raw.get("password", "")).strip(),
        password_env=str(
            email_raw.get("password_env", "ADMISSION_RADAR_SMTP_PASSWORD")
        ).strip(),
        from_address=str(email_raw.get("from_address", "")).strip(),
        to_addresses=tuple(
            item.strip() for item in recipients_raw if item.strip()
        ),
        subject_prefix=str(
            email_raw.get("subject_prefix", "[招生公告监控]")
        ).strip(),
        timeout_seconds=int(email_raw.get("timeout_seconds", 30)),
    )
    if not 1 <= email.smtp_port <= 65535:
        raise ConfigError("email.smtp_port 必须在 1 到 65535 之间。")
    _validate_email(email)

    websites_raw = root.get("websites")
    if not isinstance(websites_raw, list) or not websites_raw:
        raise ConfigError("“websites”必须是至少包含一个网站的数组。")

    websites: list[WebsiteConfig] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(websites_raw, start=1):
        website_raw = _expect_object(item, f"websites[{index}]")
        context = f"websites[{index}]"
        website = WebsiteConfig(
            id=_required_text(website_raw, "id", context),
            name=_required_text(website_raw, "name", context),
            url=_required_text(website_raw, "url", context),
            parser=_required_text(website_raw, "parser", context),
        )
        if website.id in seen_ids:
            raise ConfigError(f"网站 id 重复：{website.id}")
        seen_ids.add(website.id)

        parsed_url = urlparse(website.url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ConfigError(f"{context}.url 必须是完整的 http/https 地址。")
        websites.append(website)

    return AppConfig(
        config_path=config_path,
        database_path=_resolve_local_path(
            base_dir, root.get("database_path", "data/radar.db"), "database_path"
        ),
        log_path=_resolve_local_path(
            base_dir,
            root.get("log_path", "logs/admission_radar.log"),
            "log_path",
        ),
        request=request,
        email=email,
        websites=tuple(websites),
    )

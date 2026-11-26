"""Release notification preferences and destination validation."""

import os
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, SecretStr, model_validator


Channel = Literal["email", "ntfy", "gotify"]
CHANNELS = ("email", "ntfy", "gotify")


class MailDestination(BaseModel):
    enabled: bool = False
    host: str = Field(default="", max_length=253)
    port: int = Field(default=587, ge=1, le=65535)
    encryption: Literal["starttls", "tls", "none"] = "starttls"
    username: str = Field(default="", max_length=512)
    secret: SecretStr | None = Field(default=None, max_length=2048)
    sender: str = Field(default="", max_length=320)
    recipient: str = Field(default="", max_length=320)


class PushDestination(BaseModel):
    enabled: bool = False
    url: str = Field(default="", max_length=2048)
    secret: SecretStr | None = Field(default=None, max_length=2048)


class NtfyDestination(PushDestination):
    url: str = Field(default="https://ntfy.sh", max_length=2048)
    topic: str = Field(default="", max_length=64)


class NotificationSettings(BaseModel):
    enabled: bool = False
    mode: Literal["digest", "per_book"] = "digest"
    time: str = Field(default="09:00", pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    schedule_link: Literal["independent", "after_sync"] = "independent"
    cadence: Literal["daily", "weekly"] = "daily"
    weekday: int = Field(default=0, ge=0, le=6)
    delay_minutes: Literal[15, 30, 60] = 15
    presume_released: bool = True
    calendar_url: str = Field(default="", max_length=2048)
    email: MailDestination = Field(default_factory=MailDestination)
    ntfy: NtfyDestination = Field(default_factory=NtfyDestination)
    gotify: PushDestination = Field(default_factory=PushDestination)

    @model_validator(mode="before")
    @classmethod
    def migrate_schedule(cls, values):
        if isinstance(values, dict):
            values = dict(values)
            if "enabled" not in values:
                values["enabled"] = any(
                    isinstance(values.get(channel), dict) and values[channel].get("enabled") for channel in CHANNELS
                )
            if values.get("schedule_link") == "digest" or (
                "schedule_link" not in values and values.get("sync_before_digest")
            ):
                values["schedule_link"] = "after_sync"
            elif values.get("schedule_link") == "sync":
                values["schedule_link"] = "independent"
        return values

    @property
    def after_sync(self):
        return self.mode == "digest" and self.schedule_link == "after_sync"


def web_url(value: str) -> str:
    if re.search(r"[\s\\<>\x00-\x1f\x7f]", value):
        raise ValueError("Use a valid HTTP or HTTPS URL.")
    url = urlsplit(value)
    if (url.scheme not in {"http", "https"} or not url.hostname or url.username is not None
            or url.query or url.fragment or url.port == 0):
        raise ValueError("Use an HTTP or HTTPS base URL without credentials, a query or a fragment.")
    return value.rstrip("/")


def validate_destination(channel: Channel, destination, *, admin: bool) -> None:
    secret = destination.secret.get_secret_value() if destination.secret else ""
    if channel == "email":
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", destination.host):
            raise ValueError("Enter the SMTP server hostname or IPv4 address.")
        for address in (destination.sender, destination.recipient):
            if not re.fullmatch(r"[^\s<>,;@\x00-\x1f\x7f]+@[^\s<>,;@\x00-\x1f\x7f]+", address):
                raise ValueError("Enter one sender and one recipient email address.")
        if bool(destination.username) != bool(secret):
            raise ValueError("Enter both an SMTP username and password, or leave both blank.")
        if secret and destination.encryption == "none":
            raise ValueError("SMTP authentication requires TLS or STARTTLS.")
        endpoint = f"{destination.host.lower()}:{destination.port}"
    else:
        endpoint = web_url(destination.url)
        if secret and not re.fullmatch(r"[!-~]+", secret):
            raise ValueError("Push tokens must contain printable ASCII without spaces.")
        if channel == "gotify" and not secret:
            raise ValueError("Enter a Gotify application token.")
        if channel == "ntfy" and not re.fullmatch(r"[-_A-Za-z0-9]{1,64}", destination.topic):
            raise ValueError("Enter a valid ntfy topic.")
    allowed = {entry.strip().rstrip("/") for entry in os.environ.get(
        "AUDIBLE_NOTIFICATION_ENDPOINTS", "https://ntfy.sh"
    ).split(",")}
    if not admin and endpoint not in allowed:
        raise ValueError("This server is not approved for use by regular accounts.")

"""Render and hand off release notifications to their configured providers."""

import re
import smtplib
import ssl
from email.message import EmailMessage
from html import escape

import httpx
from starlette.concurrency import run_in_threadpool

from .notification_settings import validate_destination, web_url


class DeliveryError(Exception):
    pass


def safe_link(value):
    try:
        return web_url(value or "")
    except ValueError:
        return ""


def markdown_text(value):
    return re.sub(r"([\\`*_{}\[\]()<>#+\-.!|])", r"\\\1", value)


def metadata(book):
    names = ", ".join(book["authors"])
    series = ", ".join(book["series"])
    return " · ".join(filter(None, [names, series, book["release_date"]]))


def note(book, digest_day):
    notes = []
    if book["missed_digest"]:
        notes.append("Missed yesterday’s digest.")
    elif digest_day and book["release_date"] < digest_day:
        notes.append("Earlier release.")
    if book["presumed"]:
        notes.append("Scheduled release.")
    return " ".join(notes)


def render_releases(channel, books, digest_day, calendar_url):
    if not books:
        return "AudibleCalendar test", "Your release notifications are connected.", None, calendar_url
    title = "Audiobook releases" if digest_day else books[0]["title"]
    entries, cards = [], []
    for book in books:
        book_title = book["title"]
        details = metadata(book)
        message = note(book, digest_day)
        audible = safe_link(book["audible_url"])
        image = safe_link(book["image_url"])
        if channel == "gotify":
            lines = [f"**{markdown_text(book_title)}**", markdown_text(details), message]
            if image:
                lines.append(f"![Cover](<{image}>)")
            if audible:
                lines.append(f"[Audible](<{audible}>)")
        else:
            lines = [book_title, details, message, audible]
        entries.append("\n".join(filter(None, lines)))
        if channel == "email":
            cover = f'<img src="{escape(image)}" width="80" alt="Cover artwork"><br>' if image else ""
            link = f'<a href="{escape(audible)}">Audible</a>' if audible else ""
            cards.append(f"<p>{cover}<strong>{escape(book_title)}</strong><br>"
                         f"{escape(details)}<br>{escape(message)} {link}</p>")

    suffix = ""
    if calendar_url:
        suffix = (f"[Open AudibleCalendar](<{calendar_url}>)" if channel == "gotify"
                  else f"AudibleCalendar: {calendar_url}")
        cards.append(f'<p><a href="{escape(calendar_url)}">Open AudibleCalendar</a></p>')
    body = "\n\n".join(filter(None, [*entries, suffix]))
    click = calendar_url or (safe_link(books[0]["audible_url"]) if not digest_day else "")
    return title, body, "".join(cards) or None, click


def send_mail(destination, title, body, html):
    message = EmailMessage()
    message["Subject"] = title
    message["From"] = destination.sender
    message["To"] = destination.recipient
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype="html")
    context = ssl.create_default_context()
    arguments = (destination.host, destination.port)
    if destination.encryption == "tls":
        server = smtplib.SMTP_SSL(*arguments, context=context, timeout=10)
    else:
        server = smtplib.SMTP(*arguments, timeout=10)
    try:
        if destination.encryption == "starttls":
            server.starttls(context=context)
        if destination.secret:
            server.login(destination.username, destination.secret.get_secret_value())
        server.send_message(message, from_addr=destination.sender, to_addrs=[destination.recipient])
    finally:
        # A broken connection after DATA acceptance must not trigger a resend.
        server.close()


async def deliver(channel, settings, *, admin, books=(), digest_day=None):
    destination = getattr(settings, channel)
    try:
        validate_destination(channel, destination, admin=admin)
    except ValueError as exc:
        raise DeliveryError(str(exc)) from None
    title, body, html, click = render_releases(channel, books, digest_day, settings.calendar_url)
    title = " ".join(title.split())[:200] or "Audiobook release"
    if channel == "email":
        try:
            await run_in_threadpool(send_mail, destination, title, body, html)
        except smtplib.SMTPAuthenticationError:
            raise DeliveryError("SMTP authentication failed.") from None
        except (OSError, smtplib.SMTPException, ValueError):
            raise DeliveryError("The SMTP server could not accept the message.") from None
        return

    token = destination.secret.get_secret_value() if destination.secret else ""
    url = destination.url.rstrip("/")
    method = "POST"
    if channel == "ntfy":
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        if len(body.encode()) > 4096:
            # ntfy accepts the full text as a file when it exceeds the inline limit.
            url += f"/{destination.topic}"
            method = "PUT"
            headers.update({"Filename": "audiobook-releases.txt"})
            params = {"title": title, "message": f"See the attachment for all {len(books)} release(s)."}
            if click:
                params["click"] = click
            request = {"content": body.encode(), "params": params}
        else:
            payload = {"topic": destination.topic, "title": title, "message": body}
            if click:
                payload["click"] = click
            if len(books) == 1 and (image := safe_link(books[0]["image_url"])):
                payload["icon"] = image
            request = {"json": payload}
    else:
        url += "/message"
        headers = {"X-Gotify-Key": token}
        display = {"click": {"url": click}} if click else {}
        if len(books) == 1 and (image := safe_link(books[0]["image_url"])):
            display["bigImageUrl"] = image
        payload = {"title": title, "message": body, "extras": {
            "client::display": {"contentType": "text/markdown"},
            "client::notification": display,
        }}
        request = {"json": payload}
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
            response = await client.request(method, url, headers=headers, **request)
    except (httpx.HTTPError, ValueError):
        raise DeliveryError("Could not reach the notification server.") from None
    if not response.is_success:
        raise DeliveryError(f"The notification server returned HTTP {response.status_code}.")

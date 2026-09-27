"""Bound ordinary request bodies and apply browser response protections."""

from starlette.responses import JSONResponse

MAX_JSON_BYTES = 2 * 1024 * 1024


class HttpSecurity:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")

        async def protected_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend([
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"same-origin"),
                    (b"x-frame-options", b"DENY"),
                ])
                if path == "/":
                    headers.append((b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"))
                if path == "/" or path.startswith("/static/"):
                    headers = [(key, value) for key, value in headers if key.lower() != b"cache-control"]
                    headers.append((b"cache-control", b"no-cache"))
                message = {**message, "headers": headers}
            await send(message)

        if path.startswith("/api/") and path != "/api/database/import" and scope["method"] in {"POST", "PUT", "PATCH"}:
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > MAX_JSON_BYTES:
                    return await JSONResponse({"detail": "Request body exceeds 2 MiB"}, status_code=413)(scope, receive, protected_send)
                if not message.get("more_body", False):
                    break
            delivered = False
            original_receive = receive

            async def bounded_receive():
                nonlocal delivered
                if delivered:
                    return await original_receive()
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}

            receive = bounded_receive
        await self.app(scope, receive, protected_send)

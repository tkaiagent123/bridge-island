"""Glue between the browser page and the local app: one request in, one response out.

It runs inside Pyodide. The app code is the same code that ``bridge-island ui`` serves; only the
transport differs (a function call instead of a socket).
"""

import base64

from bridge_island.ui import App

_app = App(0)
_HOST = {"Host": "127.0.0.1:0"}


def request(method: str, target: str, body: str) -> list:
    resp = _app.handle(method, target, _HOST, body.encode("utf-8"))
    data = resp.body
    if resp.content_type.startswith("text/html"):  # the help text is written for the local app
        data = data.replace(
            b"Close the black window that opened with it, or press Ctrl+C in it. "
            b"It only runs on your own computer and",
            b"Close this browser tab. It runs in your browser and",
        )
    return [
        resp.status,
        resp.content_type,
        resp.headers.get("Content-Disposition", ""),
        base64.b64encode(data).decode("ascii"),
    ]

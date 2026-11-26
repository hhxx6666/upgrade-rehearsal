"""This module runs inside the isolated probe container with input over stdin."""

from base64 import b64decode
from hashlib import sha256
import json
import sys
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = build_opener(ProxyHandler({}), NoRedirect())


def execute(message):
    op = message["operation"]
    headers = {}
    if "token" in message:
        headers["Authorization"] = "Bearer " + message["token"]
    suffix = ""
    path = {
        "ping": "/server/ping", "version": "/server/version", "signup": "/auth/admin-sign-up",
        "login": "/auth/login", "upload": "/assets", "asset": "/assets/", "original": "/assets/",
    }[op]
    data = None
    if op in {"signup", "login"}:
        data = json.dumps(message["fields"]).encode()
        headers["Content-Type"] = "application/json"
    elif op == "upload":
        boundary = "---------UpgradeRehearsalDemo7db3f0"
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        data = b""
        for key, value in message["fields"].items():
            data += f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
        data += (f'--{boundary}\r\nContent-Disposition: form-data; name="assetData"; filename="rehearsal.png"\r\n'
                 "Content-Type: image/png\r\n\r\n").encode()
        data += b64decode(message["png"]) + f"\r\n--{boundary}--\r\n".encode()
    elif op in {"asset", "original"}:
        suffix = message["asset_id"] + ("/original" if op == "original" else "")
    request = Request("http://immich:2283/api" + path + suffix, data=data, headers=headers)
    with OPENER.open(request, timeout=message["timeout"]) as response:
        result = {"status": response.status}
        if op == "original":
            digest = sha256()
            while chunk := response.read(256 * 1024):
                digest.update(chunk)
            result["match"] = digest.hexdigest() == message["sha256"]
        elif op != "signup":
            # Only the selected API fields cross back to the executor.
            body = json.loads(response.read(1024 * 1024))
            if op == "version":
                result["value"] = "v{major}.{minor}.{patch}".format(**body)
            else:
                key = {"ping": "res", "login": "accessToken", "upload": "id", "asset": "id"}[op]
                result["value"] = body[key]
        return result


if __name__ == "__main__":
    try:
        print(json.dumps(execute(json.load(sys.stdin))))
    except HTTPError as exc:
        print(json.dumps({"status": exc.code}))
    except Exception:
        print(json.dumps({"status": 0}))

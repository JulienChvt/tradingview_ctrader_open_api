"""One-time OAuth helper: obtains a cTrader Open API access token + refresh
token for your Pepperstone (or any cTrader-based broker) account, and lists
the accounts linked to that token.

Deliberately does NOT import config.py/settings — this script has to run
*before* CTRADER_ACCESS_TOKEN and CTRADER_REFRESH_TOKEN exist in .env, and
config.py treats those as required.

Prerequisites (manual, one-time):
  1. Register an application at https://openapi.ctrader.com — this gives
     you a Client ID and Client Secret.
  2. In that app's settings, register a redirect URI matching
     CTRADER_REDIRECT_URI below (default http://localhost:5000/callback).
  3. Set CTRADER_CLIENT_ID, CTRADER_CLIENT_SECRET, CTRADER_REDIRECT_URI, and
     CTRADER_ENVIRONMENT (demo/live) in .env.

Usage:
    cd execution-service-pepperstone
    python get_access_token.py
"""
from __future__ import annotations

import asyncio
import os
import struct
import ssl
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("CTRADER_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("CTRADER_CLIENT_SECRET", "")
REDIRECT_URI = os.getenv("CTRADER_REDIRECT_URI", "http://localhost:5000/callback")
ENVIRONMENT = os.getenv("CTRADER_ENVIRONMENT", "demo").strip().lower()

if not CLIENT_ID or not CLIENT_SECRET:
    raise SystemExit(
        "Set CTRADER_CLIENT_ID and CTRADER_CLIENT_SECRET in .env first "
        "(register an app at https://openapi.ctrader.com)."
    )

_auth_code: str | None = None


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        global _auth_code
        query = parse_qs(urlparse(self.path).query)
        code = query.get("code", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        if code:
            _auth_code = code
            self.wfile.write(b"<html><body>Authorization received \xe2\x80\x94 you can close this tab.</body></html>")
        else:
            self.wfile.write(b"<html><body>No authorization code received.</body></html>")

    def log_message(self, format, *args):  # noqa: A002 - silence default request logging
        pass


def _wait_for_auth_code() -> str:
    parsed = urlparse(REDIRECT_URI)
    port = parsed.port or 5000
    server = HTTPServer(("localhost", port), _CallbackHandler)
    print(f"Waiting for authorization redirect on {REDIRECT_URI} ...")
    while _auth_code is None:
        server.handle_request()
    return _auth_code


async def _list_linked_accounts(access_token: str) -> list:
    """Minimal, self-contained cTrader connection just to list linked
    accounts — mirrors the app-auth step of ctrader/client.py but stays
    independent of it since this script runs before tokens exist."""
    from ctrader_open_api.endpoints import EndPoints
    from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoMessage
    from ctrader_open_api.messages.OpenApiMessages_pb2 import (
        ProtoOAApplicationAuthReq,
        ProtoOAGetAccountListByAccessTokenReq,
    )
    from ctrader_open_api.protobuf import Protobuf

    host = EndPoints.PROTOBUF_LIVE_HOST if ENVIRONMENT == "live" else EndPoints.PROTOBUF_DEMO_HOST
    ssl_context = ssl.create_default_context()
    reader, writer = await asyncio.open_connection(host, EndPoints.PROTOBUF_PORT, ssl=ssl_context)

    async def send(msg) -> None:
        envelope = ProtoMessage(payload=msg.SerializeToString(), payloadType=msg.payloadType)
        data = envelope.SerializeToString()
        writer.write(struct.pack(">I", len(data)) + data)
        await writer.drain()

    async def recv():
        prefix = await reader.readexactly(4)
        (length,) = struct.unpack(">I", prefix)
        data = await reader.readexactly(length)
        envelope = ProtoMessage()
        envelope.ParseFromString(data)
        return Protobuf.extract(envelope)

    await send(ProtoOAApplicationAuthReq(clientId=CLIENT_ID, clientSecret=CLIENT_SECRET))
    await recv()  # ProtoOAApplicationAuthRes

    await send(ProtoOAGetAccountListByAccessTokenReq(accessToken=access_token))
    res = await recv()  # ProtoOAGetAccountListByAccessTokenRes

    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass

    return list(res.ctidTraderAccount)


def main() -> None:
    from ctrader_open_api.auth import Auth

    auth = Auth(CLIENT_ID, CLIENT_SECRET, REDIRECT_URI)
    auth_uri = auth.getAuthUri(scope="trading")

    print("Opening your browser to authorize this app against your cTrader/Pepperstone account...")
    print(f"If it doesn't open automatically, visit:\n{auth_uri}\n")
    webbrowser.open(auth_uri)

    code = _wait_for_auth_code()
    print("Authorization code received, exchanging for tokens...")

    token_response = auth.getToken(code)
    if "accessToken" not in token_response:
        raise SystemExit(f"Token exchange failed: {token_response}")

    access_token = token_response["accessToken"]
    refresh_token = token_response["refreshToken"]

    print("\nSuccess! Add these to execution-service-pepperstone/.env:\n")
    print(f"CTRADER_ACCESS_TOKEN={access_token}")
    print(f"CTRADER_REFRESH_TOKEN={refresh_token}")

    print(f"\nLooking up accounts linked to this token ({ENVIRONMENT})...")
    accounts = asyncio.run(_list_linked_accounts(access_token))
    if not accounts:
        print("No linked accounts found on this token.")
    else:
        print(
            "\nLinked accounts (set CTRADER_ACCOUNT_ID in .env to one of "
            "these if auto-detection picks the wrong one):"
        )
        for acct in accounts:
            kind = "LIVE" if acct.isLive else "DEMO"
            print(f"  ctidTraderAccountId={acct.ctidTraderAccountId}  ({kind})  login={acct.traderLogin}")


if __name__ == "__main__":
    main()

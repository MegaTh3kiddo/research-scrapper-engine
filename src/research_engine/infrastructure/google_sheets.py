"""Google Sheets v4 adapter using a local service-account file."""

from __future__ import annotations

import json
import os
from pathlib import Path
import time
from urllib.parse import quote

import httpx


_ROOT = "https://sheets.googleapis.com/v4/spreadsheets"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_SCOPE = "https://www.googleapis.com/auth/spreadsheets"


class GoogleSheetsError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.error_code = code
        super().__init__(f"Google Sheets {code}")


class GoogleSheetsGateway:
    def __init__(self, proxy_url: str | None = None) -> None:
        filename = os.environ.get("RESEARCH_ENGINE_GOOGLE_SERVICE_ACCOUNT_FILE")
        if not filename:
            raise GoogleSheetsError("CREDENTIALS_MISSING")
        path = Path(filename).expanduser()
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise GoogleSheetsError("CREDENTIALS_UNREADABLE") from exc
        if not isinstance(info, dict) or info.get("type") != "service_account" or info.get("token_uri") != _TOKEN_URL:
            raise GoogleSheetsError("CREDENTIALS_INVALID")
        try:
            from google.oauth2 import service_account
            from google.auth.transport.requests import Request
            import requests
        except ImportError as exc:
            raise GoogleSheetsError("DEPENDENCY_MISSING") from exc
        credentials = service_account.Credentials.from_service_account_info(info, scopes=[_SCOPE])
        session = requests.Session()
        session.trust_env = False
        if proxy_url:
            session.proxies = {"http": proxy_url, "https": proxy_url}
        try:
            credentials.refresh(Request(session))
        finally:
            session.close()
        self._client = httpx.Client(
            proxy=proxy_url, trust_env=False, timeout=30,
            headers={"Authorization": f"Bearer {credentials.token}"},
        )

    def _request(self, method: str, suffix: str, *, payload: dict | None = None,
                 params: dict | None = None) -> dict:
        for attempt in range(4):
            try:
                response = self._client.request(
                    method, _ROOT + suffix, json=payload, params=params,
                )
                if response.status_code not in (429, 500, 502, 503, 504):
                    response.raise_for_status()
                    return response.json()
            except httpx.HTTPStatusError as exc:
                # Never expose a remote error body, credentials, or full URL.
                raise GoogleSheetsError(f"HTTP{exc.response.status_code}") from None
            except httpx.RequestError as exc:
                if attempt == 3:
                    raise GoogleSheetsError("CONNECTION") from exc
            if attempt < 3:
                time.sleep(2 ** attempt)
        raise GoogleSheetsError("RETRY_EXHAUSTED")

    def ensure_tab(self, spreadsheet_id: str, title: str) -> None:
        path = "/" + spreadsheet_id
        document = self._request("GET", path, params={"fields": "sheets(properties(title))"})
        if any(sheet.get("properties", {}).get("title") == title
               for sheet in document.get("sheets", [])):
            return
        self._request("POST", path + ":batchUpdate", payload={
            "requests": [{"addSheet": {"properties": {"title": title}}}],
        })

    def read_values(self, spreadsheet_id: str, cell_range: str) -> list[list[str]]:
        result = self._request("GET", f"/{spreadsheet_id}/values/{quote(cell_range, safe='')}",
                               params={"valueRenderOption": "FORMATTED_VALUE"})
        return result.get("values", [])

    def write_ranges(self, spreadsheet_id: str, ranges: list[dict]) -> None:
        self._request("POST", f"/{spreadsheet_id}/values:batchUpdate", payload={
            "valueInputOption": "RAW", "data": ranges,
        })

    def append_rows(self, spreadsheet_id: str, cell_range: str, rows: list[list[str]]) -> None:
        self._request("POST", f"/{spreadsheet_id}/values/{quote(cell_range, safe='')}:append",
                      params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
                      payload={"majorDimension": "ROWS", "values": rows})

    def close(self) -> None:
        self._client.close()

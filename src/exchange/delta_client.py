"""Thin Delta Exchange REST client.

Auth signing per docs: HMAC-SHA256 over `method + timestamp + path + query + body`.
Public endpoints (products, tickers, orderbook, candles) do not require signing,
but the signing helper is wired up so private endpoints can be added later
without restructuring.
"""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import requests


class DeltaClient:
    def __init__(self, base_url: str, api_key: str = "", api_secret: str = "", timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "btc-paper-bot/0.1"})

    def _signed_headers(self, method: str, path: str, query_string: str = "", body: str = "") -> dict:
        timestamp = str(int(time.time()))
        signature_data = method + timestamp + path + query_string + body
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            signature_data.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "api-key": self.api_key,
            "signature": signature,
            "timestamp": timestamp,
        }

    def _public_get(self, path: str, params: dict | None = None) -> dict:
        url = self.base_url + path
        r = self.session.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def _signed_get(self, path: str, params: dict | None = None) -> dict:
        query_string = "?" + urlencode(params) if params else ""
        headers = self._signed_headers("GET", path, query_string=query_string)
        url = self.base_url + path
        r = self.session.get(url, headers=headers, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def _signed_post(self, path: str, payload: dict | None = None) -> dict:
        body = json.dumps(payload) if payload is not None else ""
        headers = self._signed_headers("POST", path, body=body)
        headers["Content-Type"] = "application/json"
        url = self.base_url + path
        r = self.session.post(url, headers=headers, data=body, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def get_products(self, contract_types: str | None = None, states: str | None = "live") -> dict:
        params: dict = {}
        if contract_types:
            params["contract_types"] = contract_types
        if states:
            params["states"] = states
        return self._public_get("/v2/products", params=params or None)

    def get_ticker(self, symbol: str) -> dict:
        return self._public_get(f"/v2/tickers/{symbol}")

    def get_tickers_bulk(
        self,
        contract_types: str | None = None,
        underlying_asset_symbols: str | None = None,
        expiry_date: str | None = None,
    ) -> dict:
        params: dict = {}
        if contract_types:
            params["contract_types"] = contract_types
        if underlying_asset_symbols:
            params["underlying_asset_symbols"] = underlying_asset_symbols
        if expiry_date:
            params["expiry_date"] = expiry_date
        return self._public_get("/v2/tickers", params=params or None)

    def get_orderbook(self, symbol: str) -> dict:
        return self._public_get(f"/v2/l2orderbook/{symbol}")

    def get_candles(self, symbol: str, resolution: str, start: int, end: int) -> dict:
        params = {"symbol": symbol, "resolution": resolution, "start": start, "end": end}
        return self._public_get("/v2/history/candles", params=params)

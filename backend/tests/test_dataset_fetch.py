"""Ошибки API организаторов: понятный текст без адреса (в нём подпись доступа sig)."""

from __future__ import annotations

import httpx
import pytest

from cqa.dataset import DatasetError, fetch_raw

URL = "https://example.test/invoke?api-version=1&sig=SECRET-SIGNATURE&group_id=DEV"


def respond(monkeypatch, handler):
    """httpx.get отвечает через handler вместо сети."""
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Client(transport=transport).get(
        url, headers=kw.get("headers")))


@pytest.mark.parametrize("status, expected", [(404, "ответило 404 для group_id=NOPE — проверьте group_id"),
                                              (500, "ответило 500 для group_id=NOPE")])
def test_http_error_is_readable_and_hides_signature(monkeypatch, status, expected):
    respond(monkeypatch, lambda request: httpx.Response(status, text="nope"))
    with pytest.raises(DatasetError) as err:
        fetch_raw(URL, "NOPE")
    assert expected in str(err.value) and "SECRET" not in str(err.value) and "https://" not in str(err.value)


def test_network_error_hides_signature(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("boom", request=request)
    respond(monkeypatch, handler)
    with pytest.raises(DatasetError) as err:
        fetch_raw(URL, "DEV")
    assert "недоступно" in str(err.value) and "SECRET" not in str(err.value)


def test_not_json(monkeypatch):
    respond(monkeypatch, lambda request: httpx.Response(200, text="<html>"))
    with pytest.raises(DatasetError, match="не JSON"):
        fetch_raw(URL, "DEV")


def test_success_passes_group_id(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"isSuccess": True, "data": {"records": []}})
    respond(monkeypatch, handler)
    assert fetch_raw(URL, "CTRL1")["isSuccess"] is True
    assert "group_id=CTRL1" in seen["url"] and "sig=SECRET-SIGNATURE" in seen["url"]   # подпись сохранена

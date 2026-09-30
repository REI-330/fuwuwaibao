"""M1-2：``POST /api/auth/guest`` 与按 Cookie 的会话隔离。

纪律：**没有会话也必须能跑**（回落 ``user_local``），所以这里两头都钉：
* 合法 Cookie → 各自拿到自己的画像，互不可见；
* 无 Cookie / 坏 Cookie → 一律回落 ``user_local``，不报 401（本机单用户形态要能用）。

``register`` / ``login`` 仍按设计返回 501（本项目不存账号密码）。
"""

from __future__ import annotations

import pytest

from backend.knowledge import GraphStore
from backend.memories import MemoryStore
from backend.server import SESSION_COOKIE, CareerApi


@pytest.fixture(scope="module")
def store() -> GraphStore:
    return GraphStore()


@pytest.fixture()
def api(store: GraphStore) -> CareerApi:
    return CareerApi(store=store, memories=MemoryStore(path=":memory:"))


def open_guest(api: CareerApi, name: str = "体验用户"):
    """走一次访客登录，返回 (user, cookies)。"""
    response: dict = {}
    status, payload = api.handle("POST", "/api/auth/guest", {}, {"displayName": name}, response=response)
    assert status == 201
    assert payload["error"] is None
    cookie = response["setCookie"]
    pair = cookie.split(";")[0]
    name_, _, value = pair.partition("=")
    assert name_ == SESSION_COOKIE
    assert "HttpOnly" in cookie
    return payload["data"]["user"], {name_: value}


def test_guest_issues_httponly_cookie(api: CareerApi) -> None:
    user, cookies = open_guest(api, "周同学")
    assert user["isGuest"] is True
    assert user["displayName"] == "周同学"
    assert user["userId"].startswith("user_")
    assert cookies[SESSION_COOKIE] == user["userId"]


def test_two_sessions_are_isolated(api: CareerApi) -> None:
    alice, alice_cookies = open_guest(api, "Alice")
    bob, bob_cookies = open_guest(api, "Bob")
    assert alice["userId"] != bob["userId"]

    api.handle("PUT", "/api/profile", {}, {"major": "自动化", "source": "manual"}, cookies=alice_cookies)
    api.handle("PUT", "/api/profile", {}, {"major": "临床医学", "source": "manual"}, cookies=bob_cookies)

    alice_profile = api.handle("GET", "/api/profile", {}, None, cookies=alice_cookies)[1]["data"]["profile"]
    bob_profile = api.handle("GET", "/api/profile", {}, None, cookies=bob_cookies)[1]["data"]["profile"]
    assert alice_profile["major"] == "自动化"
    assert bob_profile["major"] == "临床医学"
    assert alice_profile["userId"] == alice["userId"]
    assert bob_profile["userId"] == bob["userId"]


def test_missing_or_bad_cookie_falls_back_to_local(api: CareerApi) -> None:
    no_cookie = api.handle("GET", "/api/profile", {}, None)[1]["data"]["profile"]
    assert no_cookie["userId"] == "user_local"

    forged = api.handle("GET", "/api/profile", {}, None, cookies={SESSION_COOKIE: "user_deadbeefcaf"})[1]
    assert forged["data"]["profile"]["userId"] == "user_local"

    junk = api.handle("GET", "/api/profile", {}, None, cookies={SESSION_COOKIE: "'; DROP TABLE profiles;--"})[1]
    assert junk["data"]["profile"]["userId"] == "user_local"


def test_register_and_login_still_not_implemented(api: CareerApi) -> None:
    for route in ("/api/auth/register", "/api/auth/login"):
        status, payload = api.handle("POST", route, {}, {"email": "a@b.c", "password": "12345678"})
        assert status == 501
        assert payload["error"]["code"] == "NOT_IMPLEMENTED"


def test_guest_writes_no_account_row(api: CareerApi) -> None:
    """访客不落账号表：本项目根本没有账号表，别在别处偷偷造一张。"""
    open_guest(api, "匿名")
    tables = {row["name"] for row in api.memories._rows("SELECT name FROM sqlite_master WHERE type='table'")}  # noqa: SLF001
    assert not any("account" in name or "user" == name for name in tables)

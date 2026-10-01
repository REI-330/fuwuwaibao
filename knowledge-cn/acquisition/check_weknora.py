"""Runtime gate for the real WeKnora instance (no mocks, no credentials stored).

鉴权：本脚本优先用 ``WEKNORA_TOKEN``；没设就自己走一次
``POST /api/v1/auth/login``（默认账号见 ``--email/--password``，可用环境变量覆盖）。

为什么必须带鉴权：``/api/v1/models`` 挂在 ``Viewer()`` 后面，**无 token 时返回 200 + 空数组**，
看起来像「一个模型都没配」。实测踩过：不带 token 判定为「无模型」，带 token 才发现内置向量模型
（``builtin-embedding-qwen3``）其实已经注册好了。所以门禁不能用「无鉴权 200」当通过。
"""
import argparse
import json
import os
import urllib.request
import urllib.error

DEFAULT_BASE = os.environ.get("WEKNORA_URL", "http://127.0.0.1:8080")
DEFAULT_EMAIL = os.environ.get("WEKNORA_EMAIL", "agent.verify@local.test")
DEFAULT_PASSWORD = os.environ.get("WEKNORA_PASSWORD", "verify12345")


def request_json(url, token=None, payload=None, timeout=15):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.status, json.loads(response.read() or b"{}")


def resolve_token(base, explicit):
    if explicit:
        return explicit, "WEKNORA_TOKEN"
    try:
        request_json(f"{base}/api/v1/auth/register",
                     payload={"email": DEFAULT_EMAIL, "password": DEFAULT_PASSWORD,
                              "username": DEFAULT_EMAIL.split("@")[0]})
    except urllib.error.HTTPError:
        pass  # 已注册过
    _, body = request_json(f"{base}/api/v1/auth/login",
                           payload={"email": DEFAULT_EMAIL, "password": DEFAULT_PASSWORD})
    token = body.get("token")
    if not token:
        raise SystemExit("login gate failed: 登录没有返回 token")
    return token, f"login as {DEFAULT_EMAIL}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--token", default=os.environ.get("WEKNORA_TOKEN", ""))
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    try:
        status, health = request_json(base + "/health")
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"health gate HTTP error: {exc.code}; check WEKNORA_URL")
    if status != 200 or health.get("status") != "ok":
        raise SystemExit(f"health gate failed: {status} {health}")

    token, how = resolve_token(base, args.token)
    try:
        status, models = request_json(base + "/api/v1/models", token=token)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise SystemExit("authentication gate failed: token rejected")
        raise SystemExit(f"model gate HTTP error: {exc.code}")
    items = models.get("data", []) if isinstance(models, dict) else []
    embeddings = [m for m in items if str(m.get("type", "")).lower() == "embedding"]
    print(json.dumps({"health": health, "auth": how, "model_count": len(items),
                      "embedding_count": len(embeddings),
                      "embeddings": [{k: m.get(k) for k in ("id", "name", "is_default", "status")} for m in embeddings]},
                     ensure_ascii=False))
    if not embeddings:
        raise SystemExit("no embedding model configured; import and retrieval gate remains blocked")
    print("runtime gate passed")


if __name__ == "__main__":
    main()


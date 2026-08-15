# -*- coding: utf-8 -*-
"""osu! API v2 客户端：OAuth 授权、令牌管理、玩家信息与成绩查询。

仅使用标准库（urllib / json / time），无第三方依赖。
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://osu.ppy.sh/api/v2"
OAUTH_AUTHORIZE_URL = "https://osu.ppy.sh/oauth/authorize"
OAUTH_TOKEN_URL = "https://osu.ppy.sh/oauth/token"

# API v2 模式参数值 -> 文本中使用的名称
MODE_NAMES = {
    "osu": "osu!",
    "taiko": "taiko",
    "fruits": "catch",
    "mania": "mania",
}
MODES = list(MODE_NAMES.keys())


class OsuApiError(Exception):
    """携带 HTTP 状态码的 API 错误。code 为 0 表示网络类错误。"""

    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(message)


class OsuApi:
    def __init__(self, client_id, client_secret, redirect_uri):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    # ---------- HTTP 基础 ----------
    @staticmethod
    def _request_json(url, data=None, access_token=None, timeout=30):
        headers = {"Accept": "application/json"}
        if access_token:
            headers["Authorization"] = "Bearer " + access_token
        if data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            req = urllib.request.Request(url, data=body, headers=headers)
        else:
            req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise OsuApiError(exc.code, "HTTP %s: %s" % (exc.code, detail)) from exc
        except urllib.error.URLError as exc:
            raise OsuApiError(0, "网络错误: %s" % (exc.reason,)) from exc

    # ---------- OAuth ----------
    def build_authorize_url(self, state):
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": "identify public",
            "state": state,
        }
        return OAUTH_AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)

    def exchange_code(self, code):
        return self._request_json(OAUTH_TOKEN_URL, data={
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
        })

    def refresh_token(self, refresh_token):
        return self._request_json(OAUTH_TOKEN_URL, data={
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        })

    # ---------- 令牌与请求 ----------
    @staticmethod
    def _token_valid(account):
        return bool(account.get("access_token")) and time.time() < account.get("expires_at", 0) - 60

    def _ensure_token(self, account):
        if self._token_valid(account):
            return
        if not account.get("refresh_token"):
            raise OsuApiError(401, "缺少 refresh_token，请删除账号后重新绑定")
        resp = self.refresh_token(account["refresh_token"])
        account["access_token"] = resp.get("access_token", "")
        if resp.get("refresh_token"):
            account["refresh_token"] = resp["refresh_token"]
        account["expires_at"] = time.time() + int(resp.get("expires_in", 86400))

    def _call(self, path, account, params=None):
        self._ensure_token(account)
        url = API_BASE + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        try:
            return self._request_json(url, access_token=account["access_token"])
        except OsuApiError as exc:
            if exc.code == 401:
                # 强制刷新后重试一次
                account["expires_at"] = 0
                self._ensure_token(account)
                return self._request_json(url, access_token=account["access_token"])
            raise

    # ---------- 业务接口 ----------
    def get_me(self, account):
        return self._call("/me", account)

    def get_user(self, account, mode):
        return self._call("/users/%s" % account["user_id"], account, {"mode": mode})

    def get_recent_scores(self, account, mode, limit=1):
        return self._call(
            "/users/%s/scores/recent" % account["user_id"],
            account,
            {"mode": mode, "limit": limit},
        )

    def get_best_scores(self, account, mode, limit=100):
        return self._call(
            "/users/%s/scores/best" % account["user_id"],
            account,
            {"mode": mode, "limit": limit},
        )

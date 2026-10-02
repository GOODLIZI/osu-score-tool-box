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

# API v2 ruleset_id -> 模式名（用于谱面属性计算等）
RULESET_IDS = {0: "osu", 1: "taiko", 2: "fruits", 3: "mania"}

# 谱面背景图（assets.ppy.sh 是公开 CDN，下载不需要鉴权）
# full  = fullsize.jpg（谱师上传的背景原图，未裁剪）
# cover = cover@2x.jpg（裁剪缩略图，缺图时回退 cover.jpg）
BACKGROUND_KINDS = {"full": "背景原图", "cover": "裁剪缩略图"}
_BACKGROUND_SIZES = {"full": ("fullsize",), "cover": ("cover@2x", "cover")}


def background_urls(beatmapset_id, kind):
    """按种类返回背景图候选 URL（按顺序尝试，前面的优先）。"""
    sizes = _BACKGROUND_SIZES.get(kind) or _BACKGROUND_SIZES["full"]
    return ["https://assets.ppy.sh/beatmaps/%s/covers/%s.jpg" % (beatmapset_id, size)
            for size in sizes]


class OsuApiError(Exception):
    """携带 HTTP 状态码的 API 错误。code 为 0 表示网络类错误。"""

    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(message)


class OsuApi:
    def __init__(self, client_id, client_secret, redirect_uri, on_token_refresh=None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        # 刷新令牌成功后的回调（用于把轮换后的 refresh_token 落盘，避免丢失）
        self.on_token_refresh = on_token_refresh

    # ---------- HTTP 基础 ----------
    @staticmethod
    def _request_json(url, data=None, access_token=None, timeout=30, api_version="20220705"):
        headers = {"Accept": "application/json"}
        if data is None and api_version:
            headers["x-api-version"] = api_version
        if access_token:
            headers["Authorization"] = "Bearer " + access_token
        if data is not None:
            # doseq=True：列表参数展开为重复键（如 mods[]=DT&mods[]=HD）
            body = urllib.parse.urlencode(data, doseq=True).encode("utf-8")
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
        if self.on_token_refresh:
            try:
                self.on_token_refresh(account)
            except Exception:
                pass

    def _call(self, path, account, params=None, method="GET"):
        self._ensure_token(account)
        url = API_BASE + path
        data = None
        if method == "POST":
            data = params
            params = None
        if params:
            # doseq=True：列表参数展开为重复键（如 mods[]=DT&mods[]=HD）
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        try:
            return self._request_json(url, data=data, access_token=account["access_token"])
        except OsuApiError as exc:
            if exc.code == 401:
                # 强制刷新后重试一次
                account["expires_at"] = 0
                self._ensure_token(account)
                return self._request_json(url, data=data, access_token=account["access_token"])
            raise

    # ---------- 业务接口 ----------
    def get_me(self, account):
        return self._call("/me", account)

    def get_user(self, account, mode=None):
        # 模式必须作为路径参数（/users/{id}/{mode}）；写成查询参数 ?mode= 会被 API 忽略，
        # 导致切换模式时玩家信息（pp/时长等）始终停留在主模式的统计上。
        path = "/users/%s" % account["user_id"]
        if mode:
            path += "/%s" % mode
        return self._call(path, account)

    def get_user_lookup(self, account, identifier, mode=None):
        """按用户 ID 或用户名查任意玩家（identifier 为字符串）。

        osu! API 的 /users/{user} 同时接受数字 ID 与用户名（用户名大小写不敏感），
        这里直接对 identifier 做 URL 编码后拼进路径。
        """
        path = "/users/%s" % urllib.parse.quote(str(identifier))
        if mode:
            path += "/%s" % mode
        return self._call(path, account)

    def get_best_scores_for(self, account, identifier, mode, limit=100, offset=0):
        """获取任意玩家（按 ID 或用户名）的最佳成绩，按 pp 降序。

        成绩接口只接受数字 ID；传入用户名时先解析为 ID。
        """
        ident = str(identifier)
        if not ident.isdigit():
            user = self.get_user_lookup(account, ident)
            ident = str(user.get("id") or ident)
        params = {"mode": mode, "limit": limit}
        if offset:
            params["offset"] = offset
        return self._call(
            "/users/%s/scores/best" % urllib.parse.quote(ident),
            account,
            params,
        )

    def get_recent_scores(self, account, mode, limit=1, offset=0):
        # 默认同时返回 stable(legacy) 与 lazer(solo) 成绩；来源可通过成绩的 best_id 区分
        # 注意：最近成绩仅包含最近 24 小时，且 offset + limit 不能超过 100
        params = {"mode": mode, "limit": limit}
        if offset:
            params["offset"] = offset
        return self._call(
            "/users/%s/scores/recent" % account["user_id"],
            account,
            params,
        )

    def get_best_scores(self, account, mode, limit=100, offset=0):
        # 默认同时返回 stable(legacy) 与 lazer(solo) 成绩；来源可通过成绩的 best_id 区分
        params = {"mode": mode, "limit": limit}
        if offset:
            params["offset"] = offset
        return self._call(
            "/users/%s/scores/best" % account["user_id"],
            account,
            params,
        )

    def get_beatmap(self, account, beatmap_id):
        """获取单个谱面信息（包含 beatmapset_id，用于定位背景图）。"""
        return self._call("/beatmaps/%s" % beatmap_id, account)

    def get_user_beatmap_scores(self, account, beatmap_id, limit=100):
        """获取指定用户在某谱面下的全部成绩（按分数降序，分页由 limit 控制）。"""
        data = self._call(
            "/beatmaps/%s/scores/users/%s/all" % (beatmap_id, account["user_id"]),
            account,
            {"limit": limit},
        )
        return data.get("scores") or []

    def get_beatmap_attributes(self, account, beatmap_id, mods, ruleset=None):
        """获取指定谱面在给定模组下的难度属性，返回带模组星数（star_rating）。

        mods 为模组缩写列表（如 ["DT", "HD"]）；ruleset 可选（osu/taiko/fruits/mania），
        用于转换谱面时按实际游玩模式计算。mods 参数按 API 要求以 mods[]= 数组形式提交。
        """
        params = {"mods[]": mods}
        if ruleset:
            params["ruleset"] = ruleset
        # 该接口是 POST（routes/web.php: Route::post('beatmaps/{beatmap}/attributes', ...)）
        resp = self._call(
            "/beatmaps/%s/attributes" % beatmap_id,
            account,
            params,
            method="POST",
        )
        attributes = resp.get("attributes") or {}
        stars = attributes.get("star_rating")
        return stars if isinstance(stars, (int, float)) else None

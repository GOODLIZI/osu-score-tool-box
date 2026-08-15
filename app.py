# -*- coding: utf-8 -*-
"""osu! 成绩记录工具 —— Tkinter 桌面界面。

功能：
  1. OAuth 授权绑定 / 删除多个 osu! 账号
  2. 查看指定模式下玩家信息（头像 / ID / PP / 等级 / ACC / 地区 / 排名 / 游玩次数 / 时长）
  3. 一键把「最近游玩」或「最佳成绩」复制到剪贴板
"""

import json
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from PIL import Image, ImageTk

from osu_api import MODES, MODE_NAMES, OsuApi, OsuApiError

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
DEFAULT_REDIRECT_URI = "http://127.0.0.1:5050/callback"

# 常用地区代码 -> 中文名（未知代码直接显示代码本身）
COUNTRY_NAMES = {
    "CN": "中国", "HK": "中国香港", "TW": "中国台湾", "MO": "中国澳门",
    "JP": "日本", "KR": "韩国", "US": "美国", "GB": "英国", "DE": "德国",
    "FR": "法国", "CA": "加拿大", "AU": "澳大利亚", "SG": "新加坡",
    "MY": "马来西亚", "TH": "泰国", "VN": "越南", "PH": "菲律宾",
    "ID": "印度尼西亚", "RU": "俄罗斯", "BR": "巴西", "IT": "意大利",
    "ES": "西班牙", "PL": "波兰", "NL": "荷兰", "SE": "瑞典", "FI": "芬兰",
    "NO": "挪威", "DK": "丹麦", "CH": "瑞士", "AT": "奥地利", "BE": "比利时",
    "PT": "葡萄牙", "IE": "爱尔兰", "NZ": "新西兰", "IN": "印度",
}

MODE_UI_NAMES = {"osu": "osu! 标准", "taiko": "太鼓", "fruits": "接水果", "mania": "下落式"}


# ---------------------------------------------------------------- 格式化工具
def format_duration(seconds):
    """把秒数格式化为 'X天Y小时' 或 'X小时Y分钟'。"""
    if seconds is None:
        return "N/A"
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem2 = divmod(rem, 3600)
    minutes = rem2 // 60
    if days > 0:
        return "%d天%d小时" % (days, hours)
    if hours > 0:
        return "%d小时%d分钟" % (hours, minutes)
    return "%d秒" % seconds


def country_name(code):
    if not code:
        return "N/A"
    return COUNTRY_NAMES.get(code, code)


def format_geki_katu(score):
    """彩黄比：count_geki(彩) : count_katu(黄)，归一到 X:1。"""
    stats = score.get("statistics") or {}
    geki = int(stats.get("count_geki") or 0)
    katu = int(stats.get("count_katu") or 0)
    if geki == 0 and katu == 0:
        return "N/A"
    if katu == 0:
        return "∞:1"
    ratio_s = ("%.2f" % (geki / katu)).rstrip("0").rstrip(".")
    return "%s:1" % ratio_s


def format_score_line(score, index=None):
    """把一条 v2 Score 对象格式化为一行文本。"""
    beatmap = score.get("beatmap") or {}
    beatmapset = score.get("beatmapset") or {}
    title = beatmapset.get("title") or "?"
    mode = MODE_NAMES.get(beatmap.get("mode"), str(beatmap.get("mode") or "?"))
    version = beatmap.get("version") or "?"
    stars = beatmap.get("difficulty_rating")
    stars_s = "%.2f" % stars if isinstance(stars, (int, float)) else "?"
    mods = "".join(score.get("mods") or []) or "NM"
    score_val = score.get("score")
    score_s = str(score_val) if isinstance(score_val, int) else (str(score_val) if score_val else "?")
    acc = score.get("accuracy")
    acc_s = "%.2f%%" % acc if isinstance(acc, (int, float)) else "?"
    pp = score.get("pp")
    pp_s = "%.2fpp" % pp if isinstance(pp, (int, float)) else "N/A"
    gk = format_geki_katu(score)
    prefix = "%d. " % index if index is not None else ""
    return "%s%s | %s | %s | %s★ | %s | %s | %s | %s | %s" % (
        prefix, title, mode, version, stars_s, mods, score_s, acc_s, pp_s, gk,
    )


def format_scores_text(scores, indexed=False):
    lines = []
    for i, score in enumerate(scores, start=1):
        lines.append(format_score_line(score, i if indexed else None))
    return "\n".join(lines)


# ---------------------------------------------------------------- 配置读写
def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"client_id": "", "client_secret": "", "redirect_uri": DEFAULT_REDIRECT_URI, "accounts": []}


def save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- 本地授权回调服务器
def run_auth_server(port, authorize_url, timeout=180):
    """启动本地回调服务器并打开浏览器，等待 osu! 授权码。返回 (code, state)。"""
    received = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/callback":
                qs = urllib.parse.parse_qs(parsed.query)
                received["code"] = qs.get("code", [None])[0]
                received["state"] = qs.get("state", [None])[0]
                received["error"] = qs.get("error", [None])[0]
                body = "授权完成！现在可以关闭此页面并回到程序。".encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args):
            pass

    try:
        server = HTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        raise OsuApiError(0, "本地回调端口 %s 被占用：%s" % (port, exc)) from exc

    try:
        webbrowser.open(authorize_url)
        deadline = time.time() + timeout
        while time.time() < deadline:
            server.handle_request()
            if "code" in received or "error" in received:
                break
    finally:
        server.server_close()

    if received.get("error"):
        raise OsuApiError(0, "授权失败：%s" % received["error"])
    if "code" not in received:
        raise OsuApiError(0, "等待授权超时，请重试")
    return received["code"], received.get("state")


# ---------------------------------------------------------------- 主界面
class App:
    def __init__(self, root):
        self.root = root
        self.config = load_config()
        self.api = OsuApi(
            self.config.get("client_id", ""),
            self.config.get("client_secret", ""),
            self.config.get("redirect_uri", DEFAULT_REDIRECT_URI),
        )
        self.current_account = None
        self.current_mode = tk.StringVar(value="osu")
        self.avatar_img = None
        self._build_ui()
        self._reload_account_list()
        if self.config.get("accounts"):
            self.account_combo.current(0)
            self._on_account_selected()

    # ---------- UI 构建 ----------
    def _build_ui(self):
        self.root.title("osu! 成绩记录工具")
        self.root.geometry("560x680")
        self.root.minsize(520, 620)

        # 顶部：账号选择与绑定
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="账号：").pack(side="left")
        self.account_var = tk.StringVar()
        self.account_combo = ttk.Combobox(top, textvariable=self.account_var, state="readonly", width=22)
        self.account_combo.pack(side="left", padx=4)
        self.account_combo.bind("<<ComboboxSelected>>", self._on_account_selected)
        ttk.Button(top, text="绑定账号", command=self.on_bind).pack(side="left", padx=4)
        ttk.Button(top, text="删除账号", command=self.on_delete_account).pack(side="left", padx=4)
        ttk.Button(top, text="API设置", command=self.on_api_settings).pack(side="left", padx=4)

        # 玩家信息区
        info = ttk.Frame(self.root, padding=8)
        info.pack(fill="x")
        self.avatar_label = ttk.Label(info, text="未选择账号", width=18)
        self.avatar_label.pack(side="left", padx=(0, 14))
        info_right = ttk.Frame(info)
        info_right.pack(side="left", fill="x", expand=True)
        self.name_label = ttk.Label(info_right, text="", font=("Microsoft YaHei UI", 14, "bold"))
        self.name_label.pack(anchor="w")
        self.id_label = ttk.Label(info_right, text="", font=("Microsoft YaHei UI", 10))
        self.id_label.pack(anchor="w")

        # 统计区
        stats = ttk.LabelFrame(self.root, text=" 玩家信息 ", padding=10)
        stats.pack(fill="x", padx=8, pady=4)
        self.stat_labels = {}
        rows = [
            ("PP", "pp"),
            ("等级", "level"),
            ("ACC", "acc"),
            ("地区", "country"),
            ("地区排名", "country_rank"),
            ("全球排名", "global_rank"),
            ("总游玩次数", "play_count"),
            ("总游玩时长", "play_time"),
        ]
        for i, (cn, key) in enumerate(rows):
            r, c = divmod(i, 2)
            ttk.Label(stats, text="%s：" % cn).grid(
                row=r, column=c * 2, sticky="e", padx=(0, 6), pady=2)
            self.stat_labels[key] = ttk.Label(stats, text="—", font=("Microsoft YaHei UI", 10, "bold"))
            self.stat_labels[key].grid(row=r, column=c * 2 + 1, sticky="w", pady=2)
        stats.columnconfigure(1, weight=1)
        stats.columnconfigure(3, weight=1)

        # 模式切换
        mode_frame = ttk.LabelFrame(self.root, text=" 模式 ", padding=8)
        mode_frame.pack(fill="x", padx=8, pady=4)
        for i, mode in enumerate(MODES):
            ttk.Radiobutton(
                mode_frame, text=MODE_UI_NAMES[mode], value=mode,
                variable=self.current_mode, command=self._on_mode_changed,
            ).grid(row=0, column=i, padx=10, pady=4)

        # 功能按钮
        actions = ttk.Frame(self.root, padding=8)
        actions.pack(fill="x")
        ttk.Button(actions, text="最近游玩", command=self.on_recent).pack(
            side="left", padx=6, expand=True, fill="x")
        ttk.Button(actions, text="最佳成绩", command=self.on_best).pack(
            side="left", padx=6, expand=True, fill="x")
        ttk.Button(actions, text="刷新数据", command=self.on_refresh).pack(
            side="left", padx=6, expand=True, fill="x")

        # 状态栏
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(self.root, textvariable=self.status_var, anchor="w",
                  relief="sunken", padding=4).pack(side="bottom", fill="x")

    # ---------- 账号列表 ----------
    def _reload_account_list(self):
        accounts = self.config.get("accounts", [])
        self.account_combo["values"] = ["%s (ID: %s)" % (a["username"], a["user_id"]) for a in accounts]
        if accounts and not self.account_var.get():
            self.account_combo.current(0)

    def _on_account_selected(self, event=None):
        idx = self.account_combo.current()
        if idx < 0:
            self.current_account = None
            self._clear_user()
            return
        self.current_account = self.config["accounts"][idx]
        self.refresh_user()

    # ---------- 玩家信息 ----------
    def refresh_user(self):
        account = self.current_account
        if not account:
            self._set_status("请先绑定并选择一个账号")
            return
        mode = self.current_mode.get()
        self._set_status("正在获取 %s 的 %s 数据…" % (account["username"], MODE_UI_NAMES[mode]))
        self._background(
            lambda: self._fetch_user(account, mode),
            self._apply_user,
            error_msg="获取玩家信息失败",
        )

    def _fetch_user(self, account, mode):
        user = self.api.get_user(account, mode)
        avatar_bytes = None
        try:
            avatar_url = user.get("avatar_url") or "https://a.ppy.sh/%s" % user.get("id")
            avatar_bytes = self._download(avatar_url)
        except Exception:
            avatar_bytes = None
        return user, avatar_bytes

    @staticmethod
    def _download(url):
        with urllib.request.urlopen(url, timeout=15) as resp:
            return resp.read()

    def _apply_user(self, result):
        user, avatar_bytes = result
        stats = user.get("statistics") or {}
        level = stats.get("level") or {}
        self.name_label.config(text=user.get("username", "?"))
        self.id_label.config(text="用户ID：%s" % user.get("id", "?"))
        self.stat_labels["pp"].config(text=self._fmt_num(stats.get("pp")))
        progress = level.get("progress", 0)
        self.stat_labels["level"].config(
            text="%s（%.0f%%）" % (level.get("current", "?"), progress or 0))
        self.stat_labels["acc"].config(text=self._fmt_percent(stats.get("hit_accuracy")))
        self.stat_labels["country"].config(text=country_name(user.get("country_code")))
        self.stat_labels["country_rank"].config(text=self._fmt_rank(stats.get("country_rank")))
        self.stat_labels["global_rank"].config(text=self._fmt_rank(stats.get("global_rank")))
        self.stat_labels["play_count"].config(text=self._fmt_num(stats.get("play_count")))
        self.stat_labels["play_time"].config(text=format_duration(stats.get("play_time")))
        if avatar_bytes:
            self._set_avatar(avatar_bytes)
        self._save_config()  # 令牌可能已被自动刷新
        self._set_status("已显示 %s 的 %s 信息" % (user.get("username", ""), MODE_UI_NAMES[self.current_mode.get()]))

    @staticmethod
    def _fmt_num(v):
        return "N/A" if v is None else "%s" % v

    @staticmethod
    def _fmt_percent(v):
        return "N/A" if v is None else "%.2f%%" % v

    @staticmethod
    def _fmt_rank(v):
        return "未上榜" if v is None else "#%s" % v

    def _set_avatar(self, data):
        try:
            img = Image.open(BytesIO(data)).convert("RGBA")
            img.thumbnail((128, 128))
            self.avatar_img = ImageTk.PhotoImage(img)
            self.avatar_label.config(image=self.avatar_img, text="")
        except Exception:
            self.avatar_label.config(image="", text="头像加载失败")

    def _clear_user(self):
        self.name_label.config(text="")
        self.id_label.config(text="")
        self.avatar_label.config(image="", text="未选择账号")
        self.avatar_img = None
        for key in self.stat_labels:
            self.stat_labels[key].config(text="—")

    # ---------- 模式 / 刷新 ----------
    def _on_mode_changed(self):
        self.refresh_user()

    def on_refresh(self):
        self.refresh_user()

    # ---------- 最近游玩 ----------
    def on_recent(self):
        account = self._require_account()
        if not account:
            return
        mode = self.current_mode.get()
        self._set_status("正在获取最近游玩…")
        self._background(
            lambda: self.api.get_recent_scores(account, mode, limit=1),
            self._apply_recent,
            error_msg="获取最近游玩失败",
        )

    def _apply_recent(self, scores):
        self._save_config()
        if not scores:
            self._set_status("该模式暂无最近游玩记录")
            return
        text = format_scores_text(scores, indexed=False)
        self._copy_text(text)
        self._set_status("已复制最近游玩（1 条）到剪贴板")

    # ---------- 最佳成绩 ----------
    def on_best(self):
        account = self._require_account()
        if not account:
            return
        n = simpledialog.askinteger(
            "最佳成绩",
            "请输入要复制的条数 n（正整数，最多 100）：",
            parent=self.root, minvalue=1, initialvalue=10,
        )
        if n is None:
            return
        n = min(n, 100)
        mode = self.current_mode.get()
        self._set_status("正在获取最佳成绩前 %d 条…" % n)
        self._background(
            lambda: self.api.get_best_scores(account, mode, limit=n),
            lambda scores: self._apply_best(scores, n),
            error_msg="获取最佳成绩失败",
        )

    def _apply_best(self, scores, n):
        self._save_config()
        if not scores:
            self._set_status("该模式暂无最佳成绩记录")
            return
        text = format_scores_text(scores, indexed=True)
        self._copy_text(text)
        self._set_status("已复制最佳成绩（%d 条）到剪贴板" % len(scores))

    # ---------- 剪贴板 ----------
    def _copy_text(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()

    # ---------- 绑定 / 删除 / API 设置 ----------
    def on_bind(self):
        if not self.config.get("client_id") or not self.config.get("client_secret"):
            self._set_status("请先填写 API 设置")
            if not self.on_api_settings():
                return
        redirect_uri = self.config.get("redirect_uri") or DEFAULT_REDIRECT_URI
        try:
            port = urllib.parse.urlparse(redirect_uri).port or 5050
        except Exception:
            port = 5050
        state = secrets.token_urlsafe(16)
        authorize_url = self.api.build_authorize_url(state)
        self._set_status("正在打开浏览器授权…（若未弹出，请访问链接：%s）" % authorize_url)
        self._background(
            lambda: self._bind_flow(port, authorize_url, state),
            self._apply_bind,
            error_msg="绑定失败",
        )

    def _bind_flow(self, port, authorize_url, state):
        code, returned_state = run_auth_server(port, authorize_url)
        if returned_state != state:
            raise OsuApiError(0, "state 校验失败，请重试")
        token = self.api.exchange_code(code)
        account = {
            "user_id": 0,
            "username": "",
            "access_token": token.get("access_token", ""),
            "refresh_token": token.get("refresh_token", ""),
            "expires_at": time.time() + int(token.get("expires_in", 86400)),
        }
        me = self.api.get_me(account)
        account["user_id"] = int(me["id"])
        account["username"] = me.get("username") or ("ID %s" % me["id"])
        return account

    def _apply_bind(self, account):
        accounts = self.config.setdefault("accounts", [])
        accounts[:] = [a for a in accounts if a["user_id"] != account["user_id"]]
        accounts.append(account)
        self._save_config()
        self._set_status("绑定成功：%s" % account["username"])
        self._reload_account_list()
        for i, a in enumerate(accounts):
            if a["user_id"] == account["user_id"]:
                self.account_combo.current(i)
                self._on_account_selected()
                break

    def on_delete_account(self):
        account = self.current_account
        if not account:
            self._set_status("请先选择要删除的账号")
            return
        if not messagebox.askyesno("删除账号", "确定删除账号 %s 吗？" % account["username"]):
            return
        self.config["accounts"] = [a for a in self.config["accounts"] if a["user_id"] != account["user_id"]]
        self._save_config()
        self.current_account = None
        self.account_var.set("")
        self._reload_account_list()
        self._clear_user()
        self._set_status("已删除账号 %s" % account["username"])

    def on_api_settings(self):
        client_id = simpledialog.askstring(
            "API设置", "Client ID：", initialvalue=self.config.get("client_id", ""), parent=self.root)
        if client_id is None:
            return False
        client_secret = simpledialog.askstring(
            "API设置", "Client Secret：", initialvalue=self.config.get("client_secret", ""), parent=self.root)
        if client_secret is None:
            return False
        redirect_uri = simpledialog.askstring(
            "API设置", "回调地址：", initialvalue=self.config.get("redirect_uri", DEFAULT_REDIRECT_URI), parent=self.root)
        if redirect_uri is None:
            return False
        self.config["client_id"] = client_id.strip()
        self.config["client_secret"] = client_secret.strip()
        self.config["redirect_uri"] = redirect_uri.strip() or DEFAULT_REDIRECT_URI
        self._save_config()
        self.api = OsuApi(self.config["client_id"], self.config["client_secret"], self.config["redirect_uri"])
        self._set_status("API 设置已保存")
        return True

    # ---------- 通用辅助 ----------
    def _require_account(self):
        if not self.current_account:
            self._set_status("请先绑定并选择一个账号")
            return None
        return self.current_account

    def _set_status(self, text):
        self.status_var.set(text)

    def _save_config(self):
        save_config(self.config)

    def _background(self, work, on_success, error_msg="操作失败"):
        def runner():
            try:
                result = work()
            except Exception as exc:
                self.root.after(0, lambda: self._show_error(error_msg, exc))
                return
            self.root.after(0, lambda: on_success(result))

        threading.Thread(target=runner, daemon=True).start()

    def _show_error(self, prefix, exc):
        msg = "%s：%s" % (prefix, exc)
        self._set_status(msg)
        messagebox.showerror("错误", msg)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()

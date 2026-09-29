# -*- coding: utf-8 -*-
"""osu! 成绩记录工具 —— Tkinter 桌面界面。

功能：
  1. OAuth 授权绑定 / 删除多个 osu! 账号
  2. 查看指定模式下玩家信息（头像 / ID / PP / 等级 / ACC / 地区 / 排名 / 游玩次数 / 时长）
  3. 一键把「最近游玩」或「最佳成绩」复制到剪贴板
  4. 兼容 osu!stable 与 osu!lazer 两种来源的成绩，文本行中标注游戏版本（lazer / stable）
"""

import json
import math
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
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageTk

from osu_api import BACKGROUND_KINDS, MODES, MODE_NAMES, RULESET_IDS, OsuApi, OsuApiError, background_urls

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

MODE_UI_NAMES = {"osu": "osu!", "taiko": "osu! taiko", "fruits": "osu! catch", "mania": "osu! mania"}

# 会影响谱面星数的模组（带这些模组时通过 API 计算实际星数）
STAR_RATING_MODS = {"DT", "NC", "HT", "HR", "EZ"}

# 成绩文本行的字段：id -> 设置界面中的名称（曲名固定首位、不可隐藏）
FIELD_LABELS = {
    "title": "曲名",
    "mode": "模式",
    "version": "难度名",
    "stars": "星数",
    "mods": "模组",
    "score": "分数",
    "acc": "准度",
    "pp": "PP",
    "detail": "详细判定",
}
FIELD_IDS = list(FIELD_LABELS.keys())

# 谱面状态（API 返回小写值）-> 显示名
BEATMAP_STATUS_LABELS = {
    "ranked": "Ranked",
    "approved": "Approved",
    "qualified": "Qualified",
    "loved": "Loved",
    "pending": "Pending",
    "wip": "WIP",
    "graveyard": "Graveyard",
}
ACC_STYLE_LABELS = {"percent": "默认（两位小数+%）", "plain": "省略百分号", "digits4": "四位数"}
PP_STYLE_LABELS = {"int": "默认（取整）", "precise": "精确（两位小数）"}

# 成绩获取默认数量：按模式分别保存（recent = 最近成绩条数，best = 最佳成绩条数）
FETCH_LIMIT_MAX = 100
FETCH_KINDS = ("recent", "best")
FETCH_KIND_LABELS = {"recent": "最近成绩数量", "best": "最佳成绩数量"}
DEFAULT_FETCH_LIMITS = {"recent": 1, "best": 10}


def default_fetch_settings():
    """一套成绩获取设置的默认值（= 原行为：最近 1 条、最佳 10 条）。"""
    return dict(DEFAULT_FETCH_LIMITS)


def _normalize_limit(value, default):
    """把数量规范化为 1~100 的整数，非法值回退默认。"""
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(FETCH_LIMIT_MAX, num))


def normalize_fetch_settings(raw):
    """规范化一套成绩获取设置。"""
    defaults = default_fetch_settings()
    if not isinstance(raw, dict):
        return defaults
    return {kind: _normalize_limit(raw.get(kind), defaults[kind]) for kind in FETCH_KINDS}


def normalize_fetch_section(fetch):
    """按模式规范化 config['fetch'] 配置段。"""
    if not isinstance(fetch, dict):
        fetch = {}
    return {mode: normalize_fetch_settings(fetch.get(mode)) for mode in MODES}


def normalize_default_mode(value):
    """默认模式（启动时进入的模式）：非法值回退第一个模式。"""
    return value if value in MODES else MODES[0]


def normalize_image_dir(value):
    """图片保存位置：字符串，去掉首尾空白，缺失则为空。"""
    return value.strip() if isinstance(value, str) else ""


def normalize_config(config):
    """统一规范化配置（显示设置 / 获取与输出设置 / 默认模式 / 图片保存位置）。"""
    normalize_display_config(config)
    normalize_fetch_config(config)
    config["default_mode"] = normalize_default_mode(config.get("default_mode"))
    config["image_dir"] = normalize_image_dir(config.get("image_dir"))
    return config


def normalize_fetch_config(config):
    """确保 config 中每个模式都有合法的成绩获取设置。"""
    config["fetch"] = normalize_fetch_section(config.get("fetch"))
    return config


# ---------------------------------------------------------------- 指令注册表
# 新增/删除指令只需修改 COMMAND_SPECS；kind 决定取数方式（recent = 最近成绩，best = 最佳成绩）
COMMAND_SPECS = [
    {
        "name": "/r",
        "kind": "recent",
        "min_args": 0,
        "max_args": 2,
        "forms": [
            ("/r", "获取最新的 1 个成绩"),
            ("/r a", "获取最近的 a 个成绩（1 ≤ a ≤ 100）"),
            ("/r a b", "获取最近第 a 到第 b 个成绩（a < b，数量 ≤ 100）"),
        ],
    },
    {
        "name": "/bg",
        "kind": "background",
        "arg_mode": "background",
        "min_args": 2,
        "max_args": 2,
        "forms": [
            ("/bg full 谱面编号", "下载该谱面的背景原图（fullsize）"),
            ("/bg cover 谱面编号", "下载该谱面的裁剪缩略图（cover）"),
        ],
    },
    {
        "name": "/bp",
        "kind": "best",
        "min_args": 0,
        "max_args": 2,
        "forms": [
            ("/bp", "获取单曲 pp 最高的 1 个成绩"),
            ("/bp a", "获取单曲 pp 最高的 a 个成绩（1 ≤ a ≤ 100）"),
            ("/bp a b", "获取第 a 高到第 b 高的成绩（a < b，数量 ≤ 100）"),
        ],
    },
]

COMMAND_RULES = [
    "参数必须是正整数（≥ 1）；两个参数时必须 a < b。",
    "一次最多获取 %d 条成绩。" % FETCH_LIMIT_MAX,
    "指令作用于当前选中的模式。",
    "最近成绩受 osu! API 限制：只包含最近 24 小时内、且最多取到第 100 个。",
    "获取背景图前需先在「获取与输出设置」里设置图片保存位置。",
]


class InputError(Exception):
    """用户输入 / 配置校验错误。"""


class CommandError(InputError):
    """指令解析或校验错误。"""


def find_command_spec(name):
    """按名称查找指令（大小写不敏感），找不到返回 None。"""
    lowered = (name or "").lower()
    for spec in COMMAND_SPECS:
        if spec["name"].lower() == lowered:
            return spec
    return None


def parse_command(text):
    """解析指令文本，返回 (spec, args)；出错抛出 CommandError。"""
    tokens = (text or "").split()
    if not tokens:
        raise CommandError("请输入指令，例如 /r 3 5 或 /bp 20。")
    spec = find_command_spec(tokens[0])
    if spec is None:
        raise CommandError("未知指令：%s\n可用指令：%s\n（可点击「指令帮助」查看全部指令）" % (
            tokens[0], "、".join(item["name"] for item in COMMAND_SPECS)))
    raw_args = tokens[1:]
    if not (spec["min_args"] <= len(raw_args) <= spec["max_args"]):
        raise CommandError("指令 %s 的参数个数不正确。\n可用形式：\n%s" % (
            spec["name"], "\n".join("    " + form + "    " + desc for form, desc in spec["forms"])))
    if spec.get("arg_mode") == "background":
        kind_raw = raw_args[0].lower()
        if kind_raw not in BACKGROUND_KINDS:
            raise CommandError("图片种类必须是 full 或 cover（例如 /bg full 1122334）。")
        number_raw = raw_args[1]
        try:
            beatmap_id = int(number_raw)
        except ValueError:
            raise CommandError("谱面编号必须是正整数：%s" % number_raw)
        if beatmap_id < 1:
            raise CommandError("谱面编号必须是正整数：%s" % number_raw)
        return spec, [kind_raw, beatmap_id]
    args = []
    for raw in raw_args:
        try:
            value = int(raw)
        except ValueError:
            raise CommandError("参数必须是正整数：%s" % raw)
        if value < 1:
            raise CommandError("参数必须是正整数：%s" % raw)
        args.append(value)
    return spec, args


def resolve_range(spec, args):
    """把指令参数解析为取数区间 (offset, limit)。"""
    if not args:
        return 0, 1
    if len(args) == 1:
        limit = args[0]
        if limit > FETCH_LIMIT_MAX:
            raise CommandError("一次最多获取 %d 条成绩（当前请求 %d 条）。" % (FETCH_LIMIT_MAX, limit))
        return 0, limit
    start, end = args
    if start >= end:
        raise CommandError("起始序号必须小于结束序号（a < b）：%d %d" % (start, end))
    limit = end - start + 1
    if limit > FETCH_LIMIT_MAX:
        raise CommandError("一次最多获取 %d 条成绩（当前请求 %d 条）。" % (FETCH_LIMIT_MAX, limit))
    return start - 1, limit


def command_help_text():
    """生成指令帮助文本（由 COMMAND_SPECS 自动生成）。"""
    lines = ["可用指令：", ""]
    for spec in COMMAND_SPECS:
        for form, desc in spec["forms"]:
            lines.append("  %-10s %s" % (form, desc))
        lines.append("")
    lines.append("通用规则：")
    lines.extend("  · " + rule for rule in COMMAND_RULES)
    return "\n".join(lines)


def default_display_settings():
    """一套显示设置的默认值（= 原行为，PP 默认取整）。"""
    return {
        "fields": list(FIELD_IDS),
        "hidden": [],
        "show_status": True,
        "show_beatmap_id": True,
        "show_mapper": True,
        "show_miss": True,
        "separate_client": True,
        "acc_style": "percent",
        "pp_style": "int",
    }


def _as_bool(value, default):
    return value if isinstance(value, bool) else default


def normalize_display_settings(raw):
    """规范化一套显示设置：剔除未知项、补齐字段、曲名固定首位、非法值回退默认。"""
    defaults = default_display_settings()
    if not isinstance(raw, dict):
        return defaults

    order = []
    raw_fields = raw.get("fields")
    for fid in raw_fields if isinstance(raw_fields, (list, tuple)) else []:
        if fid in FIELD_LABELS and fid not in order:
            order.append(fid)
    if "title" in order:
        order.remove("title")
    order.insert(0, "title")
    # 补齐新增字段：按 FIELD_IDS 的规范顺序插到相邻字段旁（而非一律追加到末尾）
    for idx, fid in enumerate(FIELD_IDS):
        if fid in order:
            continue
        pos = len(order)
        for later in FIELD_IDS[idx + 1:]:
            if later in order:
                pos = order.index(later)
                break
        order.insert(pos, fid)

    hidden = []
    raw_hidden = raw.get("hidden")
    for fid in raw_hidden if isinstance(raw_hidden, (list, tuple)) else []:
        if fid in FIELD_LABELS and fid != "title" and fid not in hidden:
            hidden.append(fid)

    acc_style = raw.get("acc_style")
    if acc_style not in ACC_STYLE_LABELS:
        acc_style = defaults["acc_style"]
    pp_style = raw.get("pp_style")
    if pp_style not in PP_STYLE_LABELS:
        pp_style = defaults["pp_style"]

    # 旧版把「游玩平台」当作一个字段：曾隐藏它的用户相当于「不单独显示平台」
    separate_default = True
    if isinstance(raw_hidden, (list, tuple)) and "client" in raw_hidden:
        separate_default = False

    return {
        "fields": order,
        "hidden": hidden,
        "show_status": _as_bool(raw.get("show_status"), True),
        "show_beatmap_id": _as_bool(raw.get("show_beatmap_id"), True),
        "show_mapper": _as_bool(raw.get("show_mapper"), True),
        "show_miss": _as_bool(raw.get("show_miss"), True),
        "separate_client": _as_bool(raw.get("separate_client"), separate_default),
        "acc_style": acc_style,
        "pp_style": pp_style,
    }


def normalize_display_section(display):
    """按模式规范化 config['display'] 配置段。"""
    if not isinstance(display, dict):
        display = {}
    return {mode: normalize_display_settings(display.get(mode)) for mode in MODES}


def normalize_display_config(config):
    """确保 config 中每个模式都有合法的显示设置。"""
    config["display"] = normalize_display_section(config.get("display"))
    return config


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


# 彩黄比：最高准度判定 : 第二高准度判定
#   osu!mania : MAX(320) : 300  -> perfect : great（旧键名 count_geki : count_300）
#   osu! / osu!taiko : 300 : 100 -> great : ok（旧键名 count_300 : count_100）
#   osu!catch : 300 : 100 -> great : large_tick_hit（旧键名 count_300 : count_100）
# 当前 API（x-api-version 20220705+）的 statistics 使用现代键名（perfect/great/good/ok/meh/miss），
# 旧版响应使用 count_*，这里两种键名都兼容。
_RATIO_FIELDS = {
    "mania": (("perfect", "count_geki"), ("great", "count_300")),
    "osu": (("great", "count_300"), ("ok", "count_100")),
    "taiko": (("great", "count_300"), ("ok", "count_100")),
    "fruits": (("great", "count_300"), ("large_tick_hit", "count_100")),
}


def _stat_value(stats, *keys):
    """从 statistics 中取第一个存在且非 0 的统计值；均不存在返回 0。"""
    for key in keys:
        val = stats.get(key)
        if isinstance(val, (int, float)) and val:
            return int(val)
    return 0


def _stat_miss(stats):
    """miss 数（现代键 miss / 旧键 count_miss），没有则为 0。"""
    return _stat_value(stats, "miss", "count_miss")


def format_judgement_detail(score, show_miss=True):
    """详细判定：彩黄比 + 可选 miss 数（无 miss 显示 FC）。

    当 300 判定（great / count_300）个数为 0 时，不显示彩黄比，仅显示 miss 数或 FC；
    关闭 miss 时若彩黄比也被抑制，则返回空串（该栏整栏省略）。
    """
    beatmap = score.get("beatmap") or {}
    mode = beatmap.get("mode")
    top_keys, second_keys = _RATIO_FIELDS.get(mode, (("great", "count_300"), ("ok", "count_100")))
    stats = score.get("statistics") or {}

    miss = _stat_miss(stats)
    miss_s = "FC" if miss == 0 else "%dmiss" % miss

    # 300 判定个数为 0：直接不显示彩黄比，仅显示 miss 数或 FC
    if _stat_value(stats, "great", "count_300") == 0:
        return miss_s if show_miss else ""

    top = _stat_value(stats, *top_keys)
    second = _stat_value(stats, *second_keys)
    if top == 0 and second == 0:
        return miss_s if show_miss else ""
    if second == 0:
        ratio_s = "∞:1"
    else:
        ratio_s = ("%.2f" % (top / second)).rstrip("0").rstrip(".") + ":1"
    return "%s %s" % (ratio_s, miss_s) if show_miss else ratio_s


def score_client(score):
    """判断一条成绩来自 osu!lazer 还是 osu!stable。

    osu! API v2 中：
    - lazer 的成绩是 Solo Score，不进入旧版排行榜，best_id 为 null；
    - stable 的成绩是 Legacy Score，best_id 为对应排行榜成绩 ID。
    """
    if score.get("best_id") is not None:
        return "stable"
    return "lazer"


def format_mods(score):
    """把 mods 字段格式化为缩写字符串（兼容 stable 的字符串数组与 lazer 的对象数组）。

    lazer 成绩中 mods 是形如 {"acronym": "HD", "settings": {...}} 的对象数组。
    """
    parts = []
    for mod in score.get("mods") or []:
        if isinstance(mod, dict):
            parts.append(mod.get("acronym") or "")
        else:
            parts.append(str(mod))
    return "".join(parts) or "NM"


def format_acc(score, style="percent"):
    """ACC 显示方式：percent -> 96.17%，plain -> 96.17，digits4 -> 9617。"""
    acc = score.get("accuracy")
    if not isinstance(acc, (int, float)):
        return "?"
    acc_pct = acc * 100 if acc <= 1 else acc  # API 的 accuracy 是 0~1 的小数
    if style == "plain":
        return "%.2f" % acc_pct
    if style == "digits4":
        # 百分数去掉小数点后取整（半进位），不补前导零
        return "%d" % int(math.floor(acc_pct * 100 + 0.5))
    return "%.2f%%" % acc_pct


def format_pp(score, style="int"):
    """PP 显示方式：int -> 155pp（四舍五入到整数），precise -> 154.75pp。"""
    pp = score.get("pp")
    if not isinstance(pp, (int, float)):
        return "N/A"
    if style == "precise":
        return "%.2fpp" % pp
    return "%dpp" % int(math.floor(pp + 0.5))


def field_visible(display, field):
    """判断某字段在当前显示设置下是否会输出。"""
    return field in (display.get("fields") or []) and field not in (display.get("hidden") or [])


def format_beatmap_status(score):
    """谱面状态（ranked / loved / graveyard ...）；缺失或未知返回空串。"""
    beatmapset = score.get("beatmapset") or {}
    beatmap = score.get("beatmap") or {}
    status = beatmapset.get("status") or beatmap.get("status")
    if not isinstance(status, str) or not status.strip():
        return ""
    status = status.strip()
    return BEATMAP_STATUS_LABELS.get(status.lower(), status.capitalize())


def format_mapper(score):
    """谱师（beatmapset.creator）；缺失返回空串。"""
    beatmapset = score.get("beatmapset") or {}
    creator = beatmapset.get("creator")
    return creator.strip() if isinstance(creator, str) and creator.strip() else ""


def render_field(field, score, display):
    """把单个字段渲染为若干段文本；无内容返回空列表。

    「模式」栏在开启「单独显示游玩平台」时会拆成两段（如 osu! 与 lazer）。
    """
    beatmap = score.get("beatmap") or {}
    beatmapset = score.get("beatmapset") or {}
    if field == "title":
        title = beatmapset.get("title") or "?"
        if display.get("show_status", True):
            status = format_beatmap_status(score)
            if status:
                return ["(%s) %s" % (status, title)]
        return [title]
    if field == "mode":
        mode = MODE_NAMES.get(beatmap.get("mode"), str(beatmap.get("mode") or "?"))
        client = score_client(score)
        if display.get("separate_client", True):
            return [mode, client]
        return ["%s (%s)" % (mode, client)]
    if field == "version":
        version = beatmap.get("version") or "?"
        beatmap_id = beatmap.get("id")
        text = version
        if display.get("show_beatmap_id", True) and isinstance(beatmap_id, int):
            text = "%s (%d)" % (text, beatmap_id)
        if display.get("show_mapper", True):
            mapper = format_mapper(score)
            if mapper:
                text = "%s by %s" % (text, mapper)
        return [text]
    if field == "stars":
        stars = score.get("_mod_star_rating")
        if stars is None:
            stars = beatmap.get("difficulty_rating")
        return ["%.2f★" % stars] if isinstance(stars, (int, float)) else ["?"]
    if field == "mods":
        return [format_mods(score)]
    if field == "score":
        total_score = score.get("total_score")
        score_val = total_score if isinstance(total_score, int) and total_score > 0 else score.get("score")
        if isinstance(score_val, int):
            return [str(score_val)]
        return [str(score_val) if score_val else "?"]
    if field == "acc":
        return [format_acc(score, display.get("acc_style", "percent"))]
    if field == "pp":
        return [format_pp(score, display.get("pp_style", "int"))]
    if field == "detail":
        return [format_judgement_detail(score, show_miss=display.get("show_miss", True))]
    return []


def format_score_line(score, index=None, display=None):
    """把一条 v2 Score 对象按显示设置格式化为一行文本（空栏整栏省略）。"""
    settings = normalize_display_settings(display) if display else default_display_settings()
    hidden = set(settings["hidden"])
    parts = []
    for field in settings["fields"]:
        if field in hidden:
            continue
        for value in render_field(field, score, settings):
            if value:
                parts.append(value)
    prefix = "%d. " % index if index is not None else ""
    return prefix + " | ".join(parts)


def format_scores_text(scores, indexed=False, display=None):
    lines = []
    for i, score in enumerate(scores, start=1):
        lines.append(format_score_line(score, i if indexed else None, display=display))
    return "\n".join(lines)


# ---------------------------------------------------------------- 配置读写
def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return normalize_config(json.load(f))
        except Exception:
            pass
    return normalize_config(
        {"client_id": "", "client_secret": "", "redirect_uri": DEFAULT_REDIRECT_URI, "accounts": []})


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
def sample_score(mode):
    """显示设置预览用的示例成绩。"""
    if mode == "mania":
        stats = {"perfect": 1440, "great": 500, "miss": 1}
    elif mode == "fruits":
        stats = {"great": 1440, "large_tick_hit": 500, "miss": 1}
    else:  # osu / taiko
        stats = {"great": 1440, "ok": 500, "miss": 1}
    return {
        "beatmap": {"id": 1234567, "mode": mode, "version": "[4K] Sample Difficulty", "difficulty_rating": 5.23},
        "beatmapset": {"title": "Sample Song", "status": "ranked", "creator": "Sample Mapper"},
        "mods": ["HD", "DT"],
        "total_score": 987654,
        "score": 987654,
        "accuracy": 0.9617,
        "pp": 154.75,
        "statistics": stats,
        "best_id": None,
    }


class DisplaySettingsDialog:
    """显示设置弹窗：按模式勾选/排序字段，配置编号、miss、ACC、PP 显示方式。"""

    def __init__(self, parent, display_config, on_save, default_mode=None):
        self.on_save = on_save
        self.draft = normalize_display_section(display_config)
        self.default_mode = normalize_default_mode(default_mode)
        self.current_mode = self.default_mode

        self.window = tk.Toplevel(parent)
        self.window.title("显示设置")
        self.window.geometry("580x730")
        self.window.minsize(540, 690)
        self.window.transient(parent)
        self.window.grab_set()

        self.mode_var = tk.StringVar(value=self.current_mode)
        self.beatmap_status_var = tk.BooleanVar()
        self.beatmap_id_var = tk.BooleanVar()
        self.mapper_var = tk.BooleanVar()
        self.separate_client_var = tk.BooleanVar()
        self.miss_var = tk.BooleanVar()
        self.acc_var = tk.StringVar()
        self.pp_var = tk.StringVar()
        self.field_vars = {fid: tk.BooleanVar() for fid in FIELD_IDS}

        self._build()
        self._load_mode(self.current_mode)

    # ---------- 界面 ----------
    def _build(self):
        mode_frame = ttk.LabelFrame(self.window, text=" 默认模式 ", padding=6)
        mode_frame.pack(fill="x", padx=10, pady=(10, 4))
        for i, mode in enumerate(MODES):
            ttk.Radiobutton(
                mode_frame, text=MODE_UI_NAMES[mode], value=mode,
                variable=self.mode_var, command=self._on_mode_change,
            ).grid(row=0, column=i, padx=6, pady=2)
        ttk.Label(mode_frame, text="启动程序时默认进入该模式，同时决定下方设置所对应的模式",
                  foreground="#666").grid(row=1, column=0, columnspan=len(MODES), sticky="w", padx=6)

        fields_frame = ttk.LabelFrame(self.window, text=" 显示字段（勾选显示，↑↓ 调整顺序） ", padding=6)
        fields_frame.pack(fill="x", padx=10, pady=4)
        self.fields_inner = ttk.Frame(fields_frame)
        self.fields_inner.pack(fill="x")

        opts = ttk.LabelFrame(self.window, text=" 选项 ", padding=8)
        opts.pack(fill="x", padx=10, pady=4)
        ttk.Checkbutton(opts, text="在曲名前显示谱面状态", variable=self.beatmap_status_var,
                        command=self._touch).grid(row=0, column=0, sticky="w", columnspan=3)
        ttk.Checkbutton(opts, text="在难度名后显示谱面编号", variable=self.beatmap_id_var,
                        command=self._touch).grid(row=1, column=0, sticky="w", columnspan=3)
        ttk.Checkbutton(opts, text="在难度名后显示谱师", variable=self.mapper_var,
                        command=self._touch).grid(row=2, column=0, sticky="w", columnspan=3)
        ttk.Checkbutton(opts, text="单独显示游玩平台（stable/lazer）", variable=self.separate_client_var,
                        command=self._touch).grid(row=3, column=0, sticky="w", columnspan=3)
        ttk.Checkbutton(opts, text="在详细判定中显示 miss 数", variable=self.miss_var,
                        command=self._touch).grid(row=4, column=0, sticky="w", columnspan=3, pady=(2, 6))
        ttk.Label(opts, text="ACC 显示方式：").grid(row=5, column=0, sticky="e")
        acc_box = ttk.Frame(opts)
        acc_box.grid(row=5, column=1, sticky="w")
        for i, style in enumerate(ACC_STYLE_LABELS):
            ttk.Radiobutton(acc_box, text=ACC_STYLE_LABELS[style], value=style,
                            variable=self.acc_var, command=self._touch).grid(row=0, column=i, padx=(0, 10))
        ttk.Label(opts, text="PP 显示方式：").grid(row=6, column=0, sticky="e", pady=(4, 0))
        pp_box = ttk.Frame(opts)
        pp_box.grid(row=6, column=1, sticky="w", pady=(4, 0))
        for i, style in enumerate(PP_STYLE_LABELS):
            ttk.Radiobutton(pp_box, text=PP_STYLE_LABELS[style], value=style,
                            variable=self.pp_var, command=self._touch).grid(row=0, column=i, padx=(0, 10))

        preview_frame = ttk.LabelFrame(self.window, text=" 预览 ", padding=8)
        preview_frame.pack(fill="x", padx=10, pady=4)
        self.preview_label = ttk.Label(preview_frame, text="", wraplength=500, justify="left")
        self.preview_label.pack(anchor="w")

        buttons = ttk.Frame(self.window, padding=10)
        buttons.pack(fill="x", side="bottom")
        ttk.Button(buttons, text="恢复本模式默认", command=self._reset_mode).pack(side="left")
        ttk.Button(buttons, text="取消", command=self.window.destroy).pack(side="right", padx=4)
        ttk.Button(buttons, text="确定", command=self._ok).pack(side="right", padx=4)

    def _rebuild_rows(self):
        for child in self.fields_inner.winfo_children():
            child.destroy()
        order = self.draft[self.current_mode]["fields"]
        for idx, fid in enumerate(order):
            row = ttk.Frame(self.fields_inner)
            row.pack(fill="x", pady=1)
            check = ttk.Checkbutton(row, text=FIELD_LABELS[fid], variable=self.field_vars[fid],
                                    command=self._touch)
            if fid == "title":
                check.state(["disabled"])
            check.pack(side="left")
            down = ttk.Button(row, text="↓", width=3, command=lambda f=fid: self._move(f, 1))
            down.pack(side="right")
            up = ttk.Button(row, text="↑", width=3, command=lambda f=fid: self._move(f, -1))
            up.pack(side="right")
            if idx == 0:
                up.state(["disabled"])
            if idx == len(order) - 1:
                down.state(["disabled"])

    # ---------- 状态同步 ----------
    def _flush(self):
        settings = self.draft[self.current_mode]
        settings["show_status"] = bool(self.beatmap_status_var.get())
        settings["show_beatmap_id"] = bool(self.beatmap_id_var.get())
        settings["show_mapper"] = bool(self.mapper_var.get())
        settings["show_miss"] = bool(self.miss_var.get())
        settings["separate_client"] = bool(self.separate_client_var.get())
        settings["acc_style"] = self.acc_var.get()
        settings["pp_style"] = self.pp_var.get()
        settings["hidden"] = [fid for fid in settings["fields"]
                              if fid != "title" and not self.field_vars[fid].get()]

    def _touch(self):
        self._flush()
        self._update_preview()

    def _move(self, fid, delta):
        self._flush()
        order = self.draft[self.current_mode]["fields"]
        i = order.index(fid)
        j = i + delta
        if 0 <= j < len(order):
            order[i], order[j] = order[j], order[i]
        self._rebuild_rows()
        self._update_preview()

    def _load_mode(self, mode):
        self.current_mode = mode
        self.mode_var.set(mode)
        self.window.title("显示设置 - %s" % MODE_UI_NAMES[mode])
        settings = self.draft[mode]
        hidden = set(settings["hidden"])
        for fid in FIELD_IDS:
            self.field_vars[fid].set(fid == "title" or fid not in hidden)
        self.beatmap_status_var.set(bool(settings.get("show_status", True)))
        self.beatmap_id_var.set(bool(settings.get("show_beatmap_id", True)))
        self.mapper_var.set(bool(settings.get("show_mapper", True)))
        self.miss_var.set(bool(settings.get("show_miss", True)))
        self.separate_client_var.set(bool(settings.get("separate_client", True)))
        self.acc_var.set(settings["acc_style"])
        self.pp_var.set(settings["pp_style"])
        self._rebuild_rows()
        self._update_preview()

    def _on_mode_change(self):
        self._flush()
        self._load_mode(self.mode_var.get())

    def _reset_mode(self):
        self.draft[self.current_mode] = default_display_settings()
        self._load_mode(self.current_mode)

    def _update_preview(self):
        self._flush()
        line = format_score_line(sample_score(self.current_mode),
                                 display=self.draft[self.current_mode])
        self.preview_label.config(
            text="%s 设置预览：\n%s" % (MODE_UI_NAMES[self.current_mode], line))

    def _ok(self):
        self._flush()
        self.on_save(self.draft, self.mode_var.get())
        self.window.destroy()


class FetchSettingsDialog:
    """「获取与输出设置」弹窗：配置成绩获取数量与图片保存位置。"""

    def __init__(self, parent, fetch_config, on_save, image_dir=""):
        self.on_save = on_save
        self.draft = normalize_fetch_section(fetch_config)
        self.image_dir = normalize_image_dir(image_dir)
        self.vars = {}  # (mode, kind) -> StringVar

        self.window = tk.Toplevel(parent)
        self.window.title("获取与输出设置")
        self.window.geometry("520x430")
        self.window.minsize(480, 410)
        self.window.transient(parent)
        self.window.grab_set()

        self._build()

    # ---------- 界面 ----------
    def _build(self):
        body = ttk.Frame(self.window, padding=12)
        body.pack(fill="both", expand=True)

        fetch_frame = ttk.LabelFrame(body, text=" 成绩获取数量 ", padding=8)
        fetch_frame.pack(fill="x")
        ttk.Label(fetch_frame, text="模式").grid(row=0, column=0, padx=6, pady=4, sticky="w")
        for col, kind in enumerate(FETCH_KINDS, start=1):
            ttk.Label(fetch_frame, text=FETCH_KIND_LABELS[kind]).grid(row=0, column=col, padx=10, pady=4)
        for row, mode in enumerate(MODES, start=1):
            ttk.Label(fetch_frame, text=MODE_UI_NAMES[mode]).grid(row=row, column=0, padx=6, pady=4, sticky="w")
            for col, kind in enumerate(FETCH_KINDS, start=1):
                var = tk.StringVar()
                self.vars[(mode, kind)] = var
                ttk.Spinbox(fetch_frame, from_=1, to=FETCH_LIMIT_MAX, width=6,
                            textvariable=var).grid(row=row, column=col, padx=10, pady=4)
        ttk.Label(fetch_frame, text="数量范围 1~%d，超出的取值会被限制。" % FETCH_LIMIT_MAX,
                  foreground="#666").grid(row=len(MODES) + 1, column=0, columnspan=3,
                                          sticky="w", padx=6, pady=(6, 0))

        image_frame = ttk.LabelFrame(body, text=" 图片保存位置 ", padding=8)
        image_frame.pack(fill="x", pady=(10, 0))
        self.image_dir_var = tk.StringVar(value=self.image_dir)
        ttk.Entry(image_frame, textvariable=self.image_dir_var).pack(
            side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(image_frame, text="选择文件夹…", command=self._choose_dir).pack(side="left")
        ttk.Label(body, text="谱面背景图会保存到该文件夹（可留空，获取时会提示设置）。",
                  foreground="#666").pack(anchor="w", pady=(4, 0))
        self._load()

        buttons = ttk.Frame(self.window, padding=10)
        buttons.pack(fill="x", side="bottom")
        ttk.Button(buttons, text="恢复默认", command=self._reset).pack(side="left")
        ttk.Button(buttons, text="取消", command=self.window.destroy).pack(side="right", padx=4)
        ttk.Button(buttons, text="确定", command=self._ok).pack(side="right", padx=4)

    # ---------- 读写 ----------
    def _load(self):
        for (mode, kind), var in self.vars.items():
            var.set(str(self.draft[mode][kind]))
        self.image_dir_var.set(self.image_dir)

    def _reset(self):
        defaults = default_fetch_settings()
        for (mode, kind), var in self.vars.items():
            var.set(str(defaults[kind]))
        self.image_dir_var.set("")

    def _choose_dir(self):
        current = (self.image_dir_var.get() or "").strip()
        initial = current if os.path.isdir(current) else os.getcwd()
        path = filedialog.askdirectory(parent=self.window, title="选择图片保存位置", initialdir=initial)
        if path:
            self.image_dir_var.set(os.path.abspath(path))

    def _collect(self):
        result = {mode: {} for mode in MODES}
        for (mode, kind), var in self.vars.items():
            raw = (var.get() or "").strip()
            label = "%s - %s" % (MODE_UI_NAMES[mode], FETCH_KIND_LABELS[kind])
            try:
                value = int(raw)
            except ValueError:
                raise InputError("「%s」不是有效整数：%s" % (label, raw))
            if not (1 <= value <= FETCH_LIMIT_MAX):
                raise InputError("「%s」必须在 1~%d 之间（当前 %d）。" % (label, FETCH_LIMIT_MAX, value))
            result[mode][kind] = value
        return result

    def _collect_image_dir(self):
        raw = (self.image_dir_var.get() or "").strip()
        if not raw:
            return ""
        path = os.path.abspath(raw)
        if not os.path.isdir(path):
            raise InputError("图片保存位置不存在：%s" % path)
        return path

    def _ok(self):
        try:
            result = self._collect()
            image_dir = self._collect_image_dir()
        except InputError as exc:
            messagebox.showwarning("获取与输出设置", str(exc), parent=self.window)
            return
        self.on_save(result, image_dir)
        self.window.destroy()


class BackgroundDialog:
    """「获取谱面背景图」弹窗：输入谱面编号并选择图片种类。"""

    def __init__(self, parent):
        self.result = None
        self.window = tk.Toplevel(parent)
        self.window.title("获取谱面背景图")
        self.window.geometry("440x210")
        self.window.minsize(420, 200)
        self.window.transient(parent)
        self.window.grab_set()

        body = ttk.Frame(self.window, padding=12)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="谱面编号：").grid(row=0, column=0, sticky="w", pady=6)
        self.id_var = tk.StringVar()
        entry = ttk.Entry(body, textvariable=self.id_var, width=20)
        entry.grid(row=0, column=1, sticky="w", pady=6)
        entry.focus_set()
        entry.bind("<Return>", lambda event: self._ok())

        ttk.Label(body, text="图片种类：").grid(row=1, column=0, sticky="w", pady=6)
        self.kind_var = tk.StringVar(value="full")
        kind_box = ttk.Frame(body)
        kind_box.grid(row=1, column=1, sticky="w", pady=6)
        for i, kind in enumerate(BACKGROUND_KINDS):
            ttk.Radiobutton(kind_box, text="%s（%s）" % (kind, BACKGROUND_KINDS[kind]), value=kind,
                            variable=self.kind_var).grid(row=0, column=i, padx=(0, 12))

        ttk.Label(body, text="full = 背景原图（fullsize）；cover = 裁剪缩略图",
                  foreground="#666").grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

        buttons = ttk.Frame(self.window, padding=10)
        buttons.pack(fill="x", side="bottom")
        ttk.Button(buttons, text="取消", command=self.window.destroy).pack(side="right", padx=4)
        ttk.Button(buttons, text="获取", command=self._ok).pack(side="right", padx=4)

    def _ok(self):
        raw = (self.id_var.get() or "").strip()
        try:
            beatmap_id = int(raw)
        except ValueError:
            messagebox.showwarning("获取谱面背景图", "请输入有效的谱面编号（正整数）。", parent=self.window)
            return
        if beatmap_id < 1:
            messagebox.showwarning("获取谱面背景图", "谱面编号必须是正整数。", parent=self.window)
            return
        self.result = (beatmap_id, self.kind_var.get())
        self.window.destroy()


class App:
    def __init__(self, root):
        self.root = root
        self.config = load_config()
        self.api = OsuApi(
            self.config.get("client_id", ""),
            self.config.get("client_secret", ""),
            self.config.get("redirect_uri", DEFAULT_REDIRECT_URI),
            on_token_refresh=self._on_token_refresh,
        )
        self.current_account = None
        self.current_mode = tk.StringVar(value=self.config.get("default_mode", "osu"))
        self.avatar_img = None
        self._star_cache = {}  # (谱面id, 模组元组, 规则) -> 带模组星数或 None
        self._build_ui()
        self._reload_account_list()
        if self.config.get("accounts"):
            self.account_combo.current(0)
            self._on_account_selected()

    # ---------- UI 构建 ----------
    def _build_ui(self):
        self.root.title("osu! 成绩记录工具")
        self.root.geometry("720x740")
        self.root.minsize(680, 680)

        # 顶部：账号选择与绑定
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="账号：").pack(side="left")
        self.account_var = tk.StringVar()
        self.account_combo = ttk.Combobox(top, textvariable=self.account_var, state="readonly", width=18)
        self.account_combo.pack(side="left", padx=4)
        self.account_combo.bind("<<ComboboxSelected>>", self._on_account_selected)
        ttk.Button(top, text="绑定账号", command=self.on_bind).pack(side="left", padx=4)
        ttk.Button(top, text="删除账号", command=self.on_delete_account).pack(side="left", padx=4)
        ttk.Button(top, text="API设置", command=self.on_api_settings).pack(side="left", padx=4)
        ttk.Button(top, text="显示设置", command=self.on_display_settings).pack(side="left", padx=4)
        ttk.Button(top, text="获取与输出设置", command=self.on_fetch_settings).pack(side="left", padx=4)

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
        ttk.Button(actions, text="获取背景图", command=self.on_get_background).pack(
            side="left", padx=6, expand=True, fill="x")

        # 指令输入
        cmd = ttk.Frame(self.root, padding=8)
        cmd.pack(fill="x")
        ttk.Label(cmd, text="指令：").pack(side="left")
        self.command_var = tk.StringVar()
        self.command_entry = ttk.Entry(cmd, textvariable=self.command_var)
        self.command_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.command_entry.bind("<Return>", lambda event: self.on_run_command())
        ttk.Button(cmd, text="执行", command=self.on_run_command).pack(side="left", padx=(0, 6))
        ttk.Button(cmd, text="指令帮助", command=self.on_command_help).pack(side="left")

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

    # ---------- 带模组星数 ----------
    @staticmethod
    def _score_mod_acronyms(score):
        """取出成绩的模组缩写列表（兼容 stable 字符串数组与 lazer 对象数组）。"""
        mods = score.get("mods") or []
        return [m.get("acronym") if isinstance(m, dict) else str(m) for m in mods]

    @staticmethod
    def _score_ruleset_name(score):
        """成绩的实际游玩模式名（osu/taiko/fruits/mania），未知返回 None。"""
        ruleset_id = score.get("ruleset_id")
        if isinstance(ruleset_id, int):
            return RULESET_IDS.get(ruleset_id)
        beatmap = score.get("beatmap") or {}
        mode = beatmap.get("mode")
        return mode if mode in MODE_NAMES else None

    def _fetch_scores(self, account, mode, fetch_fn):
        scores = fetch_fn()
        self._enrich_star_ratings(account, scores)
        return scores

    def _enrich_star_ratings(self, account, scores):
        """为含星数相关模组（DT/NC/HT/HR/EZ）的成绩补充带模组星数。

        通过 osu! API 的谱面属性接口按实际模组计算星数，结果存到 score["_mod_star_rating"]，
        失败时保留基础星数。同一 (谱面, 模组) 在本次会话内只请求一次。
        """
        for score in scores:
            mods = self._score_mod_acronyms(score)
            if not set(mods) & STAR_RATING_MODS:
                continue
            beatmap = score.get("beatmap") or {}
            beatmap_id = beatmap.get("id")
            if not isinstance(beatmap_id, int):
                continue
            ruleset = self._score_ruleset_name(score)
            key = (beatmap_id, tuple(sorted(set(mods))), ruleset)
            if key not in self._star_cache:
                try:
                    self._star_cache[key] = self.api.get_beatmap_attributes(
                        account, beatmap_id, sorted(set(mods)), ruleset=ruleset,
                    )
                except Exception:
                    self._star_cache[key] = None
            stars = self._star_cache[key]
            if stars is not None:
                score["_mod_star_rating"] = stars

    # ---------- 最近游玩 / 最佳成绩 ----------
    def on_recent(self):
        account = self._require_account()
        if not account:
            return
        mode = self.current_mode.get()
        limit = self._fetch_limits_for(mode)["recent"]
        self._set_status("正在获取最近游玩（%d 条）…" % limit)
        self._background(
            lambda: self._fetch_scores(
                account, mode, lambda: self.api.get_recent_scores(account, mode, limit=limit)),
            lambda scores: self._apply_scores(
                scores, mode, limit, "最近游玩", empty_msg="最近 24 小时内该模式暂无成绩"),
            error_msg="获取最近游玩失败",
        )

    def on_best(self):
        account = self._require_account()
        if not account:
            return
        mode = self.current_mode.get()
        limit = self._fetch_limits_for(mode)["best"]
        self._set_status("正在获取最佳成绩（%d 条）…" % limit)
        self._background(
            lambda: self._fetch_scores(
                account, mode, lambda: self.api.get_best_scores(account, mode, limit=limit)),
            lambda scores: self._apply_scores(
                scores, mode, limit, "最佳成绩", empty_msg="该模式暂无最佳成绩记录"),
            error_msg="获取最佳成绩失败",
        )

    def _apply_scores(self, scores, mode, requested, label, empty_msg="暂无成绩"):
        """格式化并复制成绩；结果多于 1 条时带序号，取不满时在状态栏说明。"""
        self._save_config()
        if not scores:
            self._set_status(empty_msg)
            return
        text = format_scores_text(scores, indexed=len(scores) > 1, display=self._display_for(mode))
        self._copy_text(text)
        got = len(scores)
        if requested and got < requested:
            self._set_status("已复制%s（请求 %d 条，实际 %d 条）到剪贴板" % (label, requested, got))
        else:
            self._set_status("已复制%s（%d 条）到剪贴板" % (label, got))

    # ---------- 指令 ----------
    def _show_command_error(self, msg):
        self._set_status("指令错误：%s" % str(msg).splitlines()[0])
        messagebox.showwarning("指令错误", str(msg), parent=self.root)

    def on_run_command(self):
        text = self.command_var.get() if hasattr(self, "command_var") else ""
        try:
            spec, args = parse_command(text)
        except CommandError as exc:
            self._show_command_error(exc)
            return
        # 背景图指令：/bg full|cover 谱面编号
        if spec["kind"] == "background":
            img_kind, beatmap_id = args
            self._start_background_download(beatmap_id, img_kind)
            return
        try:
            offset, limit = resolve_range(spec, args)
        except CommandError as exc:
            self._show_command_error(exc)
            return
        account = self._require_account()
        if not account:
            return
        mode = self.current_mode.get()
        kind = spec["kind"]
        if kind == "recent":
            empty_msg = "最近 24 小时内该模式暂无成绩"
            fetch = lambda: self.api.get_recent_scores(account, mode, limit=limit, offset=offset)
        else:
            empty_msg = "该模式暂无最佳成绩记录"
            fetch = lambda: self.api.get_best_scores(account, mode, limit=limit, offset=offset)
        self._set_status("正在获取成绩（%s，从第 %d 条起共 %d 条）…" % (spec["name"], offset + 1, limit))
        self._background(
            lambda: self._fetch_scores(account, mode, fetch),
            lambda scores: self._apply_scores(scores, mode, limit, "指令成绩", empty_msg=empty_msg),
            error_msg="获取成绩失败",
        )

    def on_command_help(self):
        messagebox.showinfo("指令帮助", command_help_text(), parent=self.root)

    # ---------- 剪贴板 ----------    # ---------- 剪贴板 ----------
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

    # ---------- 显示设置 ----------
    def _display_for(self, mode):
        """取指定模式的显示设置（已归一化）。"""
        section = self.config.get("display")
        if not isinstance(section, dict) or mode not in section:
            self.config["display"] = normalize_display_section(section)
            section = self.config["display"]
        return section[mode]

    def _apply_display_settings(self, display, default_mode=None):
        self.config["display"] = normalize_display_section(display)
        self.config["default_mode"] = normalize_default_mode(default_mode)
        self._save_config()
        self._set_status("显示设置已保存（默认模式：%s，下次启动生效）"
                         % MODE_UI_NAMES[self.config["default_mode"]])

    def on_display_settings(self):
        dialog = DisplaySettingsDialog(
            self.root, self.config.get("display"), self._apply_display_settings,
            default_mode=self.config.get("default_mode"),
        )
        self.root.wait_window(dialog.window)

    # ---------- 成绩获取设置 ----------
    def _fetch_limits_for(self, mode):
        """取指定模式的成绩获取数量设置（已归一化）。"""
        section = self.config.get("fetch")
        if not isinstance(section, dict) or mode not in section:
            self.config["fetch"] = normalize_fetch_section(section)
            section = self.config["fetch"]
        return section[mode]

    def _apply_fetch_settings(self, fetch, image_dir=None):
        self.config["fetch"] = normalize_fetch_section(fetch)
        self.config["image_dir"] = normalize_image_dir(image_dir)
        self._save_config()
        self._set_status("获取与输出设置已保存（按模式分别生效）")

    def on_fetch_settings(self):
        dialog = FetchSettingsDialog(
            self.root, self.config.get("fetch"), self._apply_fetch_settings,
            image_dir=self.config.get("image_dir"),
        )
        self.root.wait_window(dialog.window)

    # ---------- 谱面背景图 ----------
    def _resolve_image_dir(self):
        """检查图片保存位置；未设置时提示并直接打开「获取与输出设置」。"""
        image_dir = normalize_image_dir(self.config.get("image_dir"))
        if not image_dir:
            self._set_status("请设置下载路径后获取背景图")
            messagebox.showwarning("提示", "请设置下载路径后获取背景图", parent=self.root)
            self.on_fetch_settings()
            return None
        if not os.path.isdir(image_dir):
            msg = "图片保存位置不存在：%s" % image_dir
            self._set_status(msg)
            messagebox.showerror("错误", msg, parent=self.root)
            return None
        return image_dir

    def on_get_background(self):
        if self._resolve_image_dir() is None:
            return
        dialog = BackgroundDialog(self.root)
        self.root.wait_window(dialog.window)
        if not dialog.result:
            return
        beatmap_id, kind = dialog.result
        self._start_background_download(beatmap_id, kind)

    def _start_background_download(self, beatmap_id, kind):
        image_dir = self._resolve_image_dir()
        if image_dir is None:
            return
        account = self._require_account()
        if not account:
            return
        self._set_status("正在获取谱面背景图（%s %s）…" % (beatmap_id, kind))
        self._background(
            lambda: self._fetch_background(account, beatmap_id, kind, image_dir),
            self._apply_background,
            error_msg="获取背景图失败",
        )

    def _fetch_background(self, account, beatmap_id, kind, image_dir):
        """解析 beatmapset_id 并下载背景图，返回保存路径。"""
        beatmap = self.api.get_beatmap(account, beatmap_id)
        set_id = beatmap.get("beatmapset_id")
        if not set_id:
            raise OsuApiError(0, "该谱面没有关联的谱面集，无法获取背景图")
        for url in background_urls(set_id, kind):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "osu-scores-tool"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = resp.read()
            except Exception:
                continue
            if not data:
                continue
            path = os.path.join(image_dir, "%d %s.jpg" % (beatmap_id, kind))
            try:
                with open(path, "wb") as f:
                    f.write(data)
            except OSError as exc:
                raise OsuApiError(0, "无法写入文件：%s" % exc)
            return path
        raise OsuApiError(0, "该谱面没有可下载的%s" % BACKGROUND_KINDS.get(kind, "背景图"))

    def _apply_background(self, path):
        self._save_config()
        self._set_status("已保存背景图：%s" % path)

    # ---------- 令牌持久化 ----------
    def _on_token_refresh(self, account):
        """令牌刷新后立即落盘：refresh token 是一次性轮换的，不落盘会导致需要重新绑定。"""
        try:
            self.root.after(0, self._save_config)
        except Exception:
            pass

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
                # 注意：except 绑定的变量在 except 块结束时会失效，
                # 必须先用默认参数把它绑定到延迟回调上，否则 after() 执行时会 NameError。
                error = exc
                self.root.after(0, lambda err=error: self._show_error(error_msg, err))
                return
            self.root.after(0, lambda res=result: on_success(res))

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

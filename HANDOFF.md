# HANDOFF —— osu! 游玩成绩获取工具 · 交接文档

> 文档更新时间：2026-09-29
> 仓库：`master` 分支，最新提交 `c22f579`（交接时工作区干净）
> 面向对象：接手继续开发的 AI / 工程师。**请先读第 6 节「已知问题 / 临时方案 / 不要改的地方」再动手。**
> 相关文档：`README.md`（用户向说明）、`指令说明.md`（指令手册，与程序内「指令帮助」保持一致）

---

## 1. 项目目标

一个**纯本地 Windows 桌面小工具**（Tkinter GUI，非网页、无服务端、无数据库），用途：

1. 用 osu! OAuth 绑定**多个**账号；
2. 查看玩家资料（头像 / 用户名 / ID / PP / 等级 / ACC / 地区 / 地区排名 / 全球排名 / 游玩次数 / 游玩时长）——**这些统计按模式独立**；
3. 一键把「最近成绩 / 最佳成绩」按**用户自定义格式**输出到**剪贴板**或 **txt 文件**；
4. 通过内置指令按范围取成绩、下载谱面背景图；
5. 高度可配置：输出列与顺序、谱面状态 / 谱面编号 / 谱师 / miss、ACC / PP 显示格式、默认模式、取数条数、图片与成绩的保存位置。

设计取向：**本地优先、少依赖、零外部服务**。除 `Pillow`（显示头像）外全部使用 Python 标准库；所有状态保存在同目录的 `config.json`。

---

## 2. 技术栈与运行环境

| 项 | 说明 |
| --- | --- |
| 语言 | Python 3.10+（开发机实际使用 3.13：`C:\Users\GOODLIZI\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`） |
| GUI | `tkinter` / `ttk`（含经典 `tk.Button`，见 6.6） |
| 第三方依赖 | **仅 `Pillow`（PIL）**，用于头像缩放显示（`Image`、`ImageTk`） |
| 网络 | 标准库 `urllib.request`（无 requests） |
| 外部服务 | osu! API v2（`https://osu.ppy.sh/api/v2`）、OAuth（`/oauth/authorize`、`/oauth/token`）、公开 CDN `https://assets.ppy.sh`（背景图，无需鉴权）、`https://a.ppy.sh/{user_id}`（头像） |
| 授权范围 | `identify public` |
| 配置存储 | `config.json`（**已被 `.gitignore` 排除**，内含 client_secret 与访问/刷新令牌） |
| 版本控制 | git（`master`），提交历史见第 9 节 |

---

## 3. 目录结构

```
osu!游玩成绩记录程序/
├─ app.py             # 主程序：常量、格式化、三个弹窗、App 主类（约 1770 行，UTF-8 无 BOM）
├─ osu_api.py         # osu! API v2 客户端：OAuth、令牌、请求封装、各业务接口（UTF-8 带 BOM）
├─ config.json        # 运行时生成/更新：凭证、令牌、显示设置、取数设置、路径、默认模式（未纳入 git）
├─ README.md          # 用户向使用说明（带 BOM）
├─ 指令说明.md         # 指令手册；新增/删除指令必须同步更新（带 BOM）
├─ run.bat            # 启动脚本（先试 codex 运行时 python，再退回 python / py）
└─ .gitignore         # 忽略 config.json、__pycache__/、*.pyc
```

> 目录里没有 `tests/`、没有 `requirements.txt`、没有打包脚本——见第 5、7 节。

---

## 4. 当前状态

### 4.1 当前任务
用户已**暂停开发**并交接。上一轮完成的工作是：修复「主界面切换模式时玩家信息不刷新」的 bug、在模式选择处标注玩家主模式、并做了一次完整回归 + 本地提交。**当前没有未完成的开发任务**。

### 4.2 已完成（按功能）

| 功能 | 要点 |
| --- | --- |
| 多账号 OAuth 绑定 | 本地回调服务器（默认 `http://127.0.0.1:5050/callback`），绑定/删除账号，令牌自动刷新并**落盘**（见 6.1） |
| 玩家信息面板 | 头像、用户名、ID、PP、等级（含进度）、ACC、地区、地区/全球排名、游玩次数、游玩时长。**按模式独立**（`/users/{id}/{mode}`） |
| 模式切换 | 四个模式单选；玩家 **主模式**（`user.playmode`）标注「（主模式）」；切换即刷新资料 |
| 最近 / 最佳成绩 | 条数按模式配置（1~100）；结果多于 1 条自动加 `1. 2. 3.` 序号；取不满时状态栏说明实际条数 |
| 成绩输出方式 | 默认输出到**剪贴板**或 **txt 文件**（全局一项）；`/r`、`/bp` 可在末尾加 `-copy` / `-txt` 覆盖本次 |
| txt 输出 | 内容与剪贴板输出**完全一致**（UTF-8、`\n` 换行）；命名 `Bestplay/Recent + [1-a 或 a-b] + 日期(精确到分钟)`；同分钟同目录重名 → 提示「获取间隔过短」 |
| 显示设置 | 按模式分别配置：列与顺序、显示哪些列、谱面状态前缀、谱面编号、谱师、miss、是否单独显示游玩平台、ACC 三档、PP 两档；含实时预览 |
| 谱面信息 | 谱面状态（`beatmapset.status` → Ranked/Loved/…）、谱师（`beatmapset.creator`）、游玩平台（lazer/stable，由 `best_id` 判定） |
| 带模组星数 | 使用 DT/NC/HT/HR/EZ 时调用 `POST /beatmaps/{id}/attributes` 取真实星数；会话内缓存 |
| 成绩指令 | `/r`、`/r a`、`/r a b`、`/bp`、`/bp a`、`/bp a b`，注册表驱动 + 自动生成帮助 |
| 背景图指令/按钮 | `/bg full` 或 `/bg cover 谱面编号`，以及主界面「获取背景图」按钮；保存为 `<谱面编号> <种类>.jpg` |
| 获取与输出设置 | 各模式取数条数、成绩默认输出方式、成绩 txt 保存位置、背景图保存位置（可手输路径或选文件夹） |
| UI 细节 | 显示设置弹窗左右两栏 + 自适应尺寸 + 按钮靠左；「删除账号」标红并置于右下角；窗口 720×740 |

### 4.3 未完成 / 可改进（都不是 bug，属 backlog）

- **没有自动化测试**（无 `tests/`、无 CI）。历次验证都是临时脚本，见第 7 节模板。
- **没有按 ID/用户名查询「任意玩家」的功能**（只查当前绑定账号）。
- 背景图**没有「另存为」**，固定写入「获取与输出设置 → 背景图片」目录，且不会自动打开目录。
- 客栈（GD）难度的**谱师显示的是地图集作者**，不是该难度作者（见 6.4）。
- 星数属性请求**只做会话内缓存**，重启后重新请求；「最佳成绩 100 条且多为带星数模组」时会连续发较多次请求（无并发、无节流）。
- `statistics_rulesets`（一次拿全部模式统计）在实测中为 `{}`，因此每切一次模式就发一次 `/users/{id}/{mode}` 请求。
- 无异常日志文件；错误仅弹窗 + 状态栏。
- 无 i18n、无深色主题/字号设置、无 exe 打包。

### 4.4 可能新增功能（用户曾讨论/明显可扩展）

1. 按 `user_id` 或 `@用户名` 查询任意玩家（资料 + 最佳成绩），甚至两个玩家对比；
2. 下载头像 / 下载谱面 `.osz`；从成绩行一键打开谱面或预览图；
3. 收藏谱面集、最常玩谱面（`/users/{id}/beatmapsets/most_played` 等已可用）；
4. rank 历史图表（用户对象含 `rank_history`，实测 mania 有 90 个点）；
5. 新指令（如 `/firsts`、`/pinned`、`/map <id>`），只需扩注册表（见 6.7）；
6. 输出更多格式（CSV / JSON）；成绩自动增量记录到本地文件；
7. 打包为单文件 exe（PyInstaller）方便分发。

---

## 5. 关键文件、接口、数据结构

### 5.1 `osu_api.py`（API 客户端）

| 名称 | 作用 / 注意 |
| --- | --- |
| `OsuApiError(code, message)` | 统一异常；`code == 0` 表示网络类错误 |
| `OsuApi(client_id, client_secret, redirect_uri, on_token_refresh=None)` | `on_token_refresh` 在**刷新成功后**回调（用于把轮换后的 refresh_token 落盘） |
| `_request_json(...)` | 统一 urllib 请求；GET 时带 `x-api-version: 20220705`；表单体用 `urlencode(doseq=True)` |
| `_call(path, account, params=None, method="GET")` | 自动保证令牌；遇 401 强制刷新重试一次；**列表参数用 `doseq=True` 展开** |
| `build_authorize_url(state)` / `exchange_code(code)` / `refresh_token(rt)` | OAuth 三件套 |
| `get_user(account, mode=None)` | **模式必须是路径参数**：`/users/{user_id}/{mode}`（见 6.2） |
| `get_recent_scores(account, mode, limit=1, offset=0)` | 最近成绩；**API 只返回最近 24 小时内的成绩** |
| `get_best_scores(account, mode, limit=100, offset=0)` | 最佳成绩（按 pp 降序）；offset 无上限 |
| `get_beatmap(account, beatmap_id)` | 单谱面信息（取 `beatmapset_id`、`status`、`user_id` 等） |
| `get_beatmap_attributes(account, beatmap_id, mods, ruleset=None)` | **POST** `/beatmaps/{id}/attributes`，返回 `star_rating`；`mods` 以 `mods[]=` 数组提交 |
| `background_urls(beatmapset_id, kind)` | 由 `BACKGROUND_KINDS`/`_BACKGROUND_SIZES` 生成候选 URL：`full`→`fullsize.jpg`；`cover`→`cover@2x.jpg`（回退 `cover.jpg`） |
| `MODES` / `MODE_NAMES` / `RULESET_IDS` | `("osu","taiko","fruits","mania")`；界面名映射；ruleset_id→模式名 |

### 5.2 `app.py`（GUI + 业务）

| 分层 | 关键符号 |
| --- | --- |
| 常量 | `FIELD_LABELS`/`FIELD_IDS`（输出列）、`BEATMAP_STATUS_LABELS`、`ACC_STYLE_LABELS`、`PP_STYLE_LABELS`、`FETCH_LIMIT_MAX=100`、`FETCH_KINDS`、`OUTPUT_MODES`/`OUTPUT_MODE_SUFFIX`、`STAR_RATING_MODS`、`MODE_UI_NAMES`、`COUNTRY_NAMES` |
| 配置 | `load_config()` / `save_config()`、`normalize_config()`（= display + fetch + default_mode + image_dir + score_dir + output_mode）、`normalize_display_settings/section`、`normalize_fetch_settings/section`、`normalize_default_mode`、`normalize_dir` |
| 指令注册表 | `COMMAND_SPECS`（**唯一事实来源**）、`COMMAND_RULES`、`parse_command()`、`resolve_range()`、`command_help_text()`、`CommandError`/`InputError` |
| 格式化 | `format_score_line(score, index=None, display=None)`、`format_scores_text(scores, indexed, display)`、`render_field()`（**返回列表**，模式栏在「单独显示游玩平台」时拆成两段）、`format_acc()`、`format_pp()`、`format_judgement_detail()`、`format_beatmap_status()`、`format_mapper()`、`format_mods()`、`score_client()` |
| 窗口类 | `DisplaySettingsDialog`（按模式配置列/格式，两栏 + 预览）、`FetchSettingsDialog`（取数条数 + 输出方式 + 两个保存位置）、`BackgroundDialog`（输入谱面编号 + 选种类） |
| 主类 | `App`：UI 构建 `_build_ui()`、资料 `refresh_user()/_fetch_user()/_apply_user()`、成绩 `on_recent()/on_best()/_apply_scores()`、指令 `on_run_command()`、设置 `on_display_settings()/on_fetch_settings()`、背景图 `on_get_background()/_fetch_background()`、线程 `_background()` |
| 线程模型 | 所有网络请求走 `_background(work, on_success, error_msg)` → 子线程执行 → `root.after(0, ...)` 回主线程更新 UI |

### 5.3 `config.json` 数据结构（**不入库**）

```jsonc
{
  "client_id": "...", "client_secret": "...",
  "redirect_uri": "http://127.0.0.1:5050/callback",
  "accounts": [
    { "username": "GOODLIZI", "user_id": 38163757,
      "access_token": "...", "refresh_token": "...", "expires_at": 1780000000.0 }
  ],
  "display": {                      // 每个模式一套
    "osu|taiko|fruits|mania": {
      "fields": ["title","mode","version","stars","mods","score","acc","pp","detail"],
      "hidden": ["version","stars","pp"],
      "show_status": true, "show_beatmap_id": true, "show_mapper": true,
      "show_miss": true, "separate_client": true,
      "acc_style": "percent|plain|digits4", "pp_style": "int|precise"
    }
  },
  "fetch": { "osu|taiko|fruits|mania": { "recent": 1, "best": 10 } },  // 1~100
  "default_mode": "osu|taiko|fruits|mania",   // 程序启动模式（≠ 玩家主模式）
  "image_dir": "D:\\...\\downloadosu",        // 背景图保存位置（可空）
  "score_dir": "",                            // 成绩 txt 保存位置（可空）
  "output_mode": "clipboard|txt"
}
```

**注意**：`load_config()` 会做归一化（补全缺失键、剔除未知字段、clamp 数值、非法值回退默认），所以**手工编辑 config.json 出现小错不会崩**；但也意味着非法值会被静默修正。

### 5.4 输出文本行的字段（默认顺序）

```
歌曲名 | 模式 | 游玩平台 | 难度名 | ★星数 | 模组 | 分数 | 准度% | PP | 详细判定
```

- `曲名`：可选 `(Ranked) 曲名` 前缀（`show_status`）
- `模式`：osu! / taiko / catch / mania；`游玩平台` 单独成栏时模式栏**只显示规则**，隐藏平台栏则退回 `mania (lazer)` 写法
- `难度名`：可选 ` (谱面编号)` 与 ` by 谱师`
- `详细判定`：`彩黄比 + miss/FC`；300 判定为 0 时只显示 `FC` 或 `Nmiss`（此时若关掉 miss，该栏整栏省略）
- 多条时带 `1. ` 序号（由调用方传 `indexed=len(scores) > 1`）

---

## 6. 已知问题 / 临时方案 / 不要改的地方

### 6.1 令牌轮换必须落盘（**重要，勿删**）
osu! 的 refresh token 是**一次性轮换**的：刷新后旧的立即失效。因此 `OsuApi` 有 `on_token_refresh` 回调，`App._on_token_refresh()` 通过 `root.after(0, self._save_config)` 把新令牌写回 `config.json`。

> 历史事故：早期版本刷新后不落盘，导致用户被迫「删除账号 → 重新绑定」。**任何新增的网络调用路径都不要绕过这个机制**；若在非 GUI 环境（脚本）里调用 API，请自己传 `on_token_refresh` 回调保存新令牌。

### 6.2 `get_user` 的模式必须是**路径参数**
`GET /users/{id}?mode=xxx` 会被 API **静默忽略**（永远返回主模式统计），正确写法是 `GET /users/{id}/{mode}`。该 bug 曾导致「切换模式后 PP/时长不变」，已修复，**不要改回查询参数**。

### 6.3 API 的硬性限制（写代码时必须考虑）
- `limit` 上限 100；**最近成绩只包含最近 24 小时**，且 `offset + limit ≤ 100`（offset≥100 直接返回空）；最佳成绩 offset 无上限。
- 带模组星数只能通过 `POST /beatmaps/{id}/attributes`（**POST**，非 GET）；`mods[]=` 数组形式。
- `raw.jpg`（很多老教程里的背景原图地址）**已废弃**，osu-web 生成后即删除；正确地址是 `covers/fullsize.jpg`。
- API 侧的 `covers` 只提供 8 个**裁剪缩略图**（cover/card/list/slimcover × 1x/2x），**不含 fullsize**——fullsize 只能按 URL 规则直接拼。
- 官方建议请求频率 ≤ 60 次/分钟；当前实现串行请求，无节流、无重试（除 401 重试一次）。

### 6.4 谱师取的是**地图集作者**
`format_mapper()` 用 `beatmapset.creator`。客栈（GD）难度若作者与地图集主人不同，这里显示的是地图集主人。要精确到难度作者需用 `beatmap.user_id` 再查 `/users/{id}`（会多请求，未实现）。

### 6.5 `_background()` 的异常变量绑定（勿「优化」掉）
```python
except Exception as exc:
    error = exc                                  # 必须显式转发
    self.root.after(0, lambda err=error: self._show_error(error_msg, err))
```
Python 3 会在 `except` 块结束时删除 `exc`，若直接把 `exc` 闭包进延迟回调，会得到 `NameError: cannot access free variable 'exc'`（曾真实出现）。**改这里务必保留默认参数绑定。**

另外：`root.after()` 由子线程调用时，**要求主线程正在 `mainloop()`**；纯脚本环境里直接驱动 `App` 会报 `RuntimeError: main thread is not in main loop`（这是测试环境问题，不是程序 bug）。

### 6.6 「删除账号」用经典 `tk.Button`（勿改回 ttk）
Windows 的 ttk 主题会**忽略**按钮前景色设置，所以「删除账号」改用 `tk.Button(fg="#c00000")` 才能真的显示红色。它被放在**根窗口底部、状态栏右侧**（`side="right"`），是刻意的防误触设计。

### 6.7 指令体系：`COMMAND_SPECS` 是唯一事实来源
- 程序内「指令帮助」与错误提示里的可用形式**都由 `COMMAND_SPECS`/`COMMAND_RULES` 自动生成**；新增指令 = 往列表里加一条（必要时在 `parse_command` 加 `arg_mode`、在 `on_run_command` 加 `kind` 分支）。
- **同步义务**：新增/删除指令后必须更新 `指令说明.md`（README 中也写明「未来新增指令的使用方式将直接填入指令说明.md中」）。
- 参数解析：`/r`、`/bp` 支持末尾 `-copy` / `-txt`（大小写不敏感）覆盖本次输出方式；`/bg` 不支持该后缀（会被判为参数个数错误）。

### 6.8 文件编码（容易踩坑）
- `app.py`：**UTF-8 无 BOM**
- `osu_api.py`、`README.md`、`指令说明.md`、`HANDOFF.md`：**UTF-8 带 BOM**

用脚本改动时请沿用原编码（读 `utf-8-sig` / 写回保持一致），否则会产生整文件 diff 噪音或中文乱码。

### 6.9 不要提交 `config.json`
`.gitignore` 已排除它（含 `client_secret`、`access_token`、`refresh_token`）。**任何情况下都不要 `git add -f config.json`**；如需示例请另建去敏的 `config.example.json`。

### 6.10 其它现状（不是 bug）
- 当前用户 `score_dir` 为空 → 输出方式若选 txt 会提示「请设置成绩保存路径后获取成绩」并自动打开设置界面。
- 没游玩过的模式统计为 0 / `N/A` / 等级 1（接口真实返回）。
- txt 文件用 `utf-8` + `newline="\n"` 写出（与剪贴板内容逐字一致）；如需老版记事本极端兼容可改 CRLF，但会与剪贴板内容不完全一致。

---

## 7. 测试 / 构建 / 运行命令

### 7.1 运行
```powershell
# 方式一：双击 run.bat（自动找 Python：codex 运行时 → python → py）
.\run.bat

# 方式二：直接运行
cd "C:\Users\GOODLIZI\Documents\ChatGPT\osu!游玩成绩记录程序"
& "C:\Users\GOODLIZI\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" app.py
```

### 7.2 语法自检（无副作用）
```powershell
& "<python>" -c "import py_compile; py_compile.compile('app.py', doraise=True); py_compile.compile('osu_api.py', doraise=True); print('syntax OK')"
```

### 7.3 纯函数自检（无网络，推荐模板）
仓库**没有测试框架**，历次验证都是临时脚本（跑完即删）。把下面代码保存为 `_t.py` 后执行 `<python> _t.py`：

```python
import sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import app
ok = True
def check(label, got, want=True):
    global ok
    if got != want:
        ok = False; print('FAIL', label, got, '!=', want)

check('parse /r 3 5', app.parse_command('/r 3 5')[1], [3, 5])
check('parse /bp -txt', app.parse_command('/bp 1 5 -txt')[1:3], ([1, 5], 'txt'))
check('parse /r 2 6 (mania) -copy', app.parse_command('/r 2 6 (mania) -copy')[1:], ([2, 6], 'clipboard', 'mania'))
check('parse /bp 1 5 -txt (catch)', app.parse_command('/bp 1 5 -txt (catch)')[1:], ([1, 5], 'txt', 'fruits'))
check('parse 无模式后缀', app.parse_command('/r 3')[3], None)
check('range', app.resolve_range(app.parse_command('/r')[0], [3, 5]), (2, 3))

def raises(text, want):
    try:
        app.parse_command(text)
        return False
    except app.CommandError as exc:
        return want in str(exc)
check('重复模式后缀', raises('/r (mania) (osu)', '模式后缀只能出现一次'))
check('重复输出后缀', raises('/r -copy -txt', '输出方式后缀只能出现一次'))
check('未知模式后缀', raises('/r 1 (xxx)', '未知的模式后缀'))
check('parse /show name', app.parse_command('/show name 张三')[1], ['name', '张三'])
check('parse /show id', app.parse_command('/show id 12345')[1], ['id', '12345'])
check('parse /user find', app.parse_command('/user find name 张三')[1], ['find', 'name', '张三'])
check('parse /user avatar', app.parse_command('/user avatar id 12345')[1], ['avatar', 'id', '12345'])
check('parse /user profile -txt', app.parse_command('/user profile name 张三 -txt')[1:], (['profile', 'name', '张三'], 'txt', None))
check('show 无参报错', raises('/show', '参数个数不正确'))
check('show id 非数字', raises('/show id abc', '玩家ID必须是正整数'))
check('user 子指令错误', raises('/user xxx name 张三', 'find、avatar 或 profile'))

s = app.sample_score('mania'); s['best_id'] = None
check('平台分栏', app.format_score_line(s, display=app.default_display_settings()).split(' | ')[1:3], ['mania', 'lazer'])
check('ACC digits4', app.format_acc(s, 'digits4'), '9617')
check('PP precise', app.format_pp({'pp': 154.75}, 'precise'), '154.75pp')
check('mods 自定义倍速', app.format_mods({'mods': [{'acronym': 'DT', 'settings': {'speed_change': 1.2}}]}), '1.2DT')
check('mods 多模组+', app.format_mods({'mods': [{'acronym': 'NF'}, {'acronym': 'DT', 'settings': {'speed_change': 1.2}}]}), 'NF+1.2DT')
check('自定义倍速检测', app.score_has_custom_rate({'mods': [{'acronym': 'DT', 'settings': {'speed_change': 1.2}}]}), True)
check('星数样式星号', app.star_suffix('asterisk'), '*')
check('星数样式省略', app.star_suffix('none'), '')

stamp0 = time.strftime('%Y-%m-%d %H-%M', time.localtime(0))
check('文件名含模式', app.score_output_filename('Recent', 2, 6, now=0, mode='mania'), 'Recent mania 2-6 %s.txt' % stamp0)
check('文件名按钮无模式', app.score_output_filename('Bestplay', 1, 20, now=0), 'Bestplay 1-20 %s.txt' % stamp0)
check('profile 文件名', app.user_profile_filename('GOODLIZI', 'osu', now=0), 'GOODLIZI profile osu %s.txt' % stamp0)

check('fetch clamp', app.normalize_fetch_settings({'recent': '7', 'best': 150}), {'recent': 7, 'best': 100})
check('output_mode', app.normalize_output_mode(None), 'clipboard')

_cfg = {'display': {}, 'fetch': {}, 'use_unified_dir': True, 'unified_dir': ' D:\x '}
app.normalize_config(_cfg)
check('统一位置归一化', (_cfg['use_unified_dir'], _cfg['unified_dir']), (True, 'D:\x'))

forms = [f for sp in app.COMMAND_SPECS for f, _ in sp['forms']]
h = app.command_help_text()
md = open('指令说明.md', encoding='utf-8-sig').read()
check('帮助含全部指令', all(f in h for f in forms))
check('说明文档含全部指令', all(f in md for f in forms))
print('OK' if ok else 'FAILURES')
```

### 7.4 GUI 冒烟（无网络）
```python
import tkinter as tk, app
root = tk.Tk(); root.withdraw()
o = app.App.__new__(app.App); o.root = root
o.current_mode = tk.StringVar(value='mania'); o.main_mode = None; o.mode_buttons = {}
o._build_ui()                                                                    # 主界面
d = app.DisplaySettingsDialog(root, {}, lambda *a: None, default_mode='mania')   # 显示设置
f = app.FetchSettingsDialog(root, {}, lambda *a: None)                           # 获取与输出设置
b = app.BackgroundDialog(root)                                                   # 获取背景图
root.destroy()
```

### 7.5 真实 API 端到端（联网；可能刷新令牌，务必传回调写回）
```python
api = OsuApi(cfg['client_id'], cfg['client_secret'], cfg['redirect_uri'], on_token_refresh=persist)
acc = cfg['accounts'][0]
for m in app.MODES:
    api.get_user(acc, m)                                   # 四个模式统计应各不相同
api.get_best_scores(acc, 'mania', limit=3, offset=0)       # 最佳成绩
api.get_recent_scores(acc, 'mania', limit=3)               # 最近成绩（可能为空：24h 内没玩）
api.get_beatmap_attributes(acc, 906558, ['DT'], 'osu')     # 带模组星数
app.App.__new__(app.App)._fetch_background(acc, 906558, 'full', '<dir>')   # 背景图下载
```

### 7.6 构建
无构建步骤（纯 Python 脚本）。如需分发可自行加 PyInstaller：
`pyinstaller --noconsole --onefile app.py`（注意 `config.json` 会生成在 exe 同目录）。

---

## 8. 代码风格与约束

1. **语言/注释**：全部中文注释与中文 UI 文案，标点用中文全角。
2. **依赖克制**：保持「标准库 + Pillow」；引入新库前请评估必要性与打包影响。
3. **配置优先**：用户可调项一律进 `config.json`，并且**必须**在 `normalize_*` 里给出默认值与非法值回退，保证旧配置平滑升级。
4. **按模式分治**：显示设置、取数条数都是「每模式一套」；新增同类配置请沿用 `{mode: {...}}` 结构 + `normalize_*_section()`。
5. **单一线程模型**：网络只走 `App._background()`；绝不在主线程直接发请求，也不要引入线程池（避免 Tk 线程安全问题）。
6. **错误处理**：网络错误抛 `OsuApiError`，由 `_background` 统一弹窗；用户输入错误抛 `InputError`/`CommandError`，用 `messagebox.showwarning` 提示且不中断程序。
7. **输出格式只经一条通道**：所有成绩文本必须经 `format_scores_text(..., display=...)`，不要在别处拼字符串，否则「显示设置」会失效。
8. **指令可扩展性**：新指令只改 `COMMAND_SPECS`（+ 必要的解析/分发分支），帮助界面与错误提示会自动跟随；同时更新 `指令说明.md`。
9. **命名与文案**：界面用 `MODE_UI_NAMES`（osu! / osu! taiko / osu! catch / osu! mania），不要把内部 key（osu/taiko/fruits/mania）直接展示给用户。
10. **改动后必做**：① 语法编译；② 纯函数自检；③ GUI 冒烟；④ 涉及网络的改动跑一次真实 API 端到端；⑤ 若影响输出格式/指令，同步 `README.md` 与 `指令说明.md`；⑥ 确认 `git status` 不含 `config.json`。
11. **提交习惯**：提交信息用中文，概括性标题 + `-` 列表正文（参考 `git log`）。

---

## 9. 交接清单（接手第一步建议）

1. 通读 `app.py` 的常量区 → `COMMAND_SPECS` → `format_*` → `App`（可先看第 5 节符号表）；
2. 通读 `osu_api.py`（约 200 行，很短）；
3. 运行 `run.bat`，确认能启动并拉到玩家信息；
4. 若 `config.json` 缺失或令牌失效：程序内「API设置」填 Client ID/Secret → 「绑定账号」重新授权；
5. 改动前先 `git log --oneline` 了解演进：
   `660a8a6`（首版）→ `56d98f1`（显示/取数设置 + 指令 + 指令说明.md）→ `1b9b8aa`（背景图下载）→ `c22f579`（txt 输出 + 模式修复 + UI 优化）。

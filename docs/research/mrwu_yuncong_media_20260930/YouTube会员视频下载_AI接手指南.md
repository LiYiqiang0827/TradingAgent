# YouTube 会员视频下载｜AI 接手指南

文档版本：v1.0（2026-10-04，Asia/Tokyo）。下载路线最后实测：2026-10-03。

适用范围：用户本人已购买对应频道会员、浏览器能正常播放的指定回放，保存到本机供个人研究。本文公开方法；登录凭据、会员视频、截图、逐字稿和任务日志留在本机或用户明确指定的私人云盘。

## 0. 接手 AI 先读这段

1. 成功路线是 **YouTube 单站 Cookie 导出 → yt-dlp Python 调用 → 匹配版本的 EJS ＋ Node → `web_safari,web_creator` → FFmpeg 合并 → 本地验收**。
2. 优先复用已有的本地 Cookie 导出。Chrome/Edge 直接读取在本机分别遇到数据库占用与 DPAPI 解密失败，不要反复走同一失败路线。
3. 已验证组合：Python 3.12、yt-dlp `2026.08.19`、yt-dlp-ejs `0.8.0`、Node `v22.23.2`，FFmpeg/ffprobe 在 PATH。
4. 项目采用 **720p 优先；关键代码、价格、坐标或账户表不可读时再取原生 1080p**。视频放 D 盘，不放 Git 工作区。
5. Cookie 只在内存中注入 yt-dlp；**不设置 `cookiefile` / `cookiesfrombrowser`，不让 yt-dlp 回写原导出文件**。
6. 只处理用户明确指定的视频清单，顺序下载、有限重试、保留续传文件；验收成功后才更新清单。
7. 登录过期、会员权限不足、Google 验证或 CAPTCHA 时，停止依赖该登录状态的作业，报告原因。不要自行更改浏览器安全设置或账户设置。
8. 本文是方法说明，不授予额外下载、转写、内容分析或发布权限；接手时仍以用户当前任务范围为准。

## 1. 已验证的结果与限制

2026-10-03 先以视频 `11uEocTvLcU` 验证访问，再顺序补下载同一频道的另外五场会员回放，共六场成功。首场得到 1280×720 H.264 视频、AAC 音轨、约 5598.89 秒的 MP4；其余五场也完成时长、音画轨道、文件 SHA-256 和抽样读取检查。

这证明上述版本及客户端组合在当时可用，不保证以后 YouTube 接口、登录状态或所有会员视频都继续适用。某一客户端失败，也不能据此认定用户没有会员权限。

浏览器播放截图只证明该浏览器会话能访问页面。下载是否成功，须检查实际落盘媒体；视频里的嵌套浏览器/Studio 画面属于录屏内容，不能当成外层页面的网址。

## 2. 输入、目录与输出约定

开始前记录以下配置；路径可按用户电脑调整。

| 项目 | 本项目约定 | 接手检查 |
| --- | --- | --- |
| 输入视频 | 明确的视频 ID / watch URL 清单 | 不自动扩展到整个频道或其他账号 |
| 身份条件 | 用户的账号具有对应会员访问权 | 用户在正常浏览器中确认能播放 |
| Cookie 文件 | `D:\YuncongVideoAnalysis\local_auth\youtube.cookies.txt` | 仅 YouTube、Netscape 格式、有效、非空 |
| 下载暂存 | `D:\YuncongVideoAnalysis\member_access_test\<job_id>\<video_id>\` | 与认证目录分开，不在 Git 仓库内 |
| 验收后源视频 | `D:\YuncongVideoAnalysis\2026-01-01_to_2026-10-03\` | 属于原任务冻结窗口；其他任务另建目录 |
| 清单及核对记录 | `D:\YuncongVideoAnalysis\ledger_2026_v0.1\` | 只改指定视频行，不覆盖其他研究结果 |
| 依赖 | 已有本地包，或任务专用虚拟环境 | 记录实际版本与 Node 路径 |

最少输出字段：视频 ID、公开 watch URL、实际文件路径、字节数、SHA-256、列示/本地时长、分辨率、音视频编码、抽样读取结果、状态及失败类别。只保存白名单元数据，不直接保存 yt-dlp 的整个 `info` 对象。

原始直播开始时间如用于后续事件定位，应单独保存官方直播 `actualStartTime` 及来源；缺失时登记待核，不从标题日期推测。

## 3. 准备依赖

### 3.1 优先复用本机已验证环境

本机历史作业的路径如下，**只用于定位，不要求别的机器复制这些布局**：

- yt-dlp 包：`%TEMP%\codex-yuncong-links-20261003`。这是临时目录，接手时先检查是否仍存在。
- EJS 包：`D:\YuncongVideoAnalysis\member_access_test\11uEocTvLcU\tools`。
- 首场脚本：`D:\YuncongVideoAnalysis\member_access_test\11uEocTvLcU\check_access.py`。
- 五场有限作业：`D:\YuncongVideoAnalysis\member_access_test\remaining_five_20261003\download_five.py`。
- 验收及本地记录在对应作业目录和 `ledger_2026_v0.1`。

首场脚本固定一个视频，五场脚本固定五个 ID；它们是历史作业，不是可输入任意 URL 的通用下载命令。需要处理新视频时先检查脚本输入范围，复用调用方式，而非直接重跑旧清单。

### 3.2 需要独立环境时

以下是在新任务目录建立相同 Python 依赖组合的示例，不要覆盖正在运行的已有环境。目录须在 Git 仓库外。

```powershell
py -3.12 -m venv 'D:\YuncongVideoAnalysis\download_tools\.venv'
& 'D:\YuncongVideoAnalysis\download_tools\.venv\Scripts\python.exe' -m pip install 'yt-dlp==2026.08.19' 'yt-dlp-ejs==0.8.0'
& 'D:\YuncongVideoAnalysis\download_tools\.venv\Scripts\python.exe' -m yt_dlp --version
ffmpeg -version
ffprobe -version
# Node 路径以实际安装位置为准；下载调用中必须显式启用它。
& 'C:\path\to\node.exe' --version
```

`C:\path\to\node.exe` 是占位路径，不能原样执行。优先选择现有、受信任的 Node 安装，已验证版本为 `22.23.2`。这里使用 Node 执行 JavaScript 挑战处理，不会启动 Hermes 或任何外部模型 worker。

EJS 版本必须符合该 yt-dlp 版本的依赖要求；升级时一起核对，不能把任意新旧版本混用。官方说明要求 Node 至少 22，并显式启用该运行时。[EJS 配置与版本匹配](https://github.com/yt-dlp/yt-dlp/wiki/EJS)

## 4. 获取并保管登录状态

### 4.1 本机实测方式

初次导出或登录过期时，由用户完成以下操作：

1. 在正常浏览器中登录自己的 Google/YouTube 账号，确认指定会员视频可以播放。
2. 使用用户已安装、确认来源的 [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) 扩展。
3. 选择 **Netscape 格式，仅导出当前 YouTube 站点**，不选 `Export All`。
4. 保存到前述 `local_auth\youtube.cookies.txt`。该目录保持本地，仅允许当前用户和 SYSTEM 访问，不放进任何同步或发布目录。

已有有效导出可复用，不需要每下载一场就重新导出或退出浏览器。AI 不要求用户把 Cookie 文件或密码发进聊天，不读取无关浏览器站点凭据，也不代替用户处理登录验证。

YouTube 可能轮换登录 Cookie。官方另有通过独立隐私窗口导出、结束该窗口会话的做法；这是需要用户操作的备选，不是本机六场下载的实测步骤，也不保证永久有效。[官方 YouTube Cookie 导出说明](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies)

### 4.2 程序读取规则

- 用 `YoutubeDLCookieJar` 在本机加载导出，再逐条注入 `ydl.cookiejar`。
- 域名只允许 `youtube.com` 及其子域。混有其他站点时拒绝，不把整浏览器登录状态导入下载器。
- Cookie 解析异常仅报告异常类型；某些解析错误会把原始行拼进异常信息，不能打印 `str(exc)` 或 traceback。
- 不输出 Cookie 值、请求头、浏览器认证数据库或完整 Cookie 行。
- 不启用 verbose 日志；清除错误信息里的带签名媒体 URL。不保存原始 `info`，其中可能含临时访问地址。
- Cookie 目录不进入 Git、云盘同步、结果打包或子 agent 输入。不要为了读取 Cookie 去禁用浏览器加密、安全防护或更改注册表。

## 5. 成功调用参数

下面是**已实测的关键 Python 调用模式**。将它放在一个已有任务脚本中，设置明确输入后使用；示例仅完成下载到暂存目录，后续仍须按第 6 节验收。

```python
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yt_dlp
from yt_dlp.cookies import YoutubeDLCookieJar

# 这些是配置项；Node 占位路径必须替换。不要在配置中填写密码或 Cookie 值。
URL = 'https://www.youtube.com/watch?v=11uEocTvLcU'
AUTH_ROOT = Path(r'D:\YuncongVideoAnalysis\local_auth').resolve()
COOKIE_PATH = AUTH_ROOT / 'youtube.cookies.txt'
OUTPUT_ROOT = Path(r'D:\YuncongVideoAnalysis\member_access_test\new_job')
NODE_PATH = Path(r'C:\path\to\node.exe')
HEIGHT = 720

parsed = urlparse(URL)
assert parsed.scheme == 'https'
assert parsed.hostname in {'youtube.com', 'www.youtube.com'}
assert parsed.path == '/watch'
video_id = parse_qs(parsed.query).get('v', [''])[0]
assert re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id)
assert HEIGHT in (720, 1080)
assert NODE_PATH.is_file(), 'Replace the Node placeholder with an installed executable'
assert COOKIE_PATH.resolve(strict=True).parent == AUTH_ROOT

stage = (OUTPUT_ROOT / video_id).resolve()
assert not stage.is_relative_to(AUTH_ROOT)
assert not AUTH_ROOT.is_relative_to(stage)
assert not any((parent / '.git').exists() for parent in (stage, *stage.parents))
stage.mkdir(parents=True, exist_ok=True)

try:
    jar = YoutubeDLCookieJar(str(COOKIE_PATH))
    jar.load()
    cookies = list(jar)
except Exception as exc:
    # 解析器错误可能含 Cookie 原文，只输出异常类型。
    print(json.dumps({'status': 'invalid_cookie_export', 'error_type': type(exc).__name__}))
    raise SystemExit(1)

if not cookies or any(
    c.domain.lstrip('.') != 'youtube.com'
    and not c.domain.lstrip('.').endswith('.youtube.com') for c in cookies
):
    raise SystemExit('Cookie export must be non-empty and YouTube-only')

class QuietLogger:
    # 示例不输出网络日志，避免泄漏认证或临时 URL。
    def debug(self, message):
        pass
    def warning(self, message):
        pass
    def error(self, message):
        pass

options = {
    'quiet': True, 'noprogress': True, 'logger': QuietLogger(),
    'noplaylist': True, 'socket_timeout': 20, 'cachedir': False,
    'extractor_retries': 0, 'retries': 2, 'fragment_retries': 2,
    'extractor_args': {'youtube': {'player_client': ['web_safari', 'web_creator']}},
    'js_runtimes': {'node': {'path': str(NODE_PATH)}},
    'format': (
        f'bv[height<={HEIGHT}][vcodec^=avc1]+ba[ext=m4a]/'
        f'b[height<={HEIGHT}][vcodec^=avc1]/'
        f'bv[height<={HEIGHT}]+ba/b[height<={HEIGHT}]'
    ),
    'outtmpl': str(stage / '%(id)s.%(ext)s'), 'merge_output_format': 'mp4',
    'concurrent_fragment_downloads': 1, 'continuedl': True, 'overwrites': False,
}

try:
    with yt_dlp.YoutubeDL(options) as ydl:
        # 不向 options 写 cookiefile / cookiesfrombrowser；不会回写原 Cookie 文件。
        for cookie in cookies:
            ydl.cookiejar.set_cookie(cookie)
        info = ydl.extract_info(URL, download=True)
    assert info['id'] == video_id
except Exception as exc:
    print(json.dumps({'stream_id': video_id, 'status': 'failed', 'error_type': type(exc).__name__}))
    raise SystemExit(1)

# 只输出白名单信息；该状态尚未代表媒体验收通过。
print(json.dumps({
    'stream_id': video_id,
    'status': 'download_returned_validation_required',
    'listed_duration_seconds': info.get('duration'),
    'stage_directory': str(stage),
}, ensure_ascii=True))
```

参数说明：

- H.264＋M4A/AAC 优先，便于本地读取；最终编码和分辨率以 ffprobe 为准。格式兜底可能选到较低高度，不能仅凭 `HEIGHT=720` 就登记为 720p。
- `merge_output_format='mp4'` 是封装合并设置，不是放大分辨率，也不是把所有视频重新压制成 H.264。
- 原导出只读，但 yt-dlp 会在其独立内存 Cookie jar 中接收会话更新；不要将它导出或上传。
- 这份可移植示例是对成功调用参数的整理，不是新发布的下载平台；已实测的成功结果来自本机历史作业脚本。
- 若需要诊断，先根据下节做只读检查；额外本地日志须去掉 Cookie、认证头、带签名 URL，Cookie 解析失败仍只输出异常类型。

## 6. 下载后验收，不凭进程返回成功就登记完成

每个文件至少完成以下检查：

1. ID 与指定清单一致；最终媒体实际存在且字节数大于零。不要将 `.part`、独立音轨或未合并视频当完成。
2. ffprobe 能读取 **视频轨道和音频轨道**；记录实际宽高、编码及本地时长。
3. 与事先保存的列表时长比较。本次允许差异 ≤2 秒；超出则待核，先排查截断、剪辑、元数据差异，不直接说“完整”。
4. 用 FFmpeg 读取中段及尾段各 8 秒，确认音画解码无错误；另抽查声音非静音。
5. 使用已有 `media_platform frames` 定点取原分辨率帧，检查需要识别的代码/名称、价格、图形或表格。空白刷新、遮挡须前后补帧；换 1080p不能恢复被遮挡内容。
6. 不可读时另取原生 1080p；若源视频本身没有该格式，登记限制，不用插值放大冒充更多信息。
7. 计算 SHA-256。若由暂存移到最终目录，核对两个绝对路径都在明确授权目录内、目标不存在，使用不覆盖的单文件移动，并复核移动后的哈希。
8. 验收通过后更新该视频的清单行、下载文件列表、源校验列表及白名单元数据，保留失败/待核状态。检查已完成 ID 可跳过，不能以同名存在就认定已验收。

检查命令示例（`VIDEO_PATH`、时间和输出路径须按当前视频设置）：

```powershell
$yuncongVideoPath = 'D:\YuncongVideoAnalysis\member_access_test\new_job\11uEocTvLcU\11uEocTvLcU.mp4'
ffprobe -v error -show_streams -show_format -of json $yuncongVideoPath
# 示例媒体约 5599 秒；其他视频先计算各自中段、尾段位置。
ffmpeg -hide_banner -v error -ss 2800 -i $yuncongVideoPath -t 8 -f null NUL
ffmpeg -hide_banner -v error -ss 5579 -i $yuncongVideoPath -t 8 -f null NUL
Get-FileHash -LiteralPath $yuncongVideoPath -Algorithm SHA256
py -3.12 -m media_platform frames $yuncongVideoPath --times '600,2400,5400' --max-frames 3 --output 'D:\YuncongVideoAnalysis\member_access_test\new_job\quality_frames'
```

帧输出目录应为新目录；若已存在，先看平台的续跑条件，不覆盖已有画面证据。Python 捕获 FFmpeg 日志时明确使用 `encoding='utf-8', errors='replace'`，避免中文路径被 Windows 默认 GBK 解码打断。

“时长相符＋抽样通过”不等于已证明整片无剪辑、逐帧无损或全文识别准确。下载验收与后续转录、内容提取、人工冻结是不同阶段。

## 7. 本机已遇到的问题及处理顺序

| 现象 | 本机结论 / 检查 | 下一步 |
| --- | --- | --- |
| Chrome Cookie 数据库无法复制 | 浏览器及残留后台进程占用 | 不反复读锁定数据库；用户关闭后最多重试一次，或用已有单站导出 |
| 完全关闭 Chrome 后仍报 DPAPI 解密失败；Edge 同样失败 | 退出解决了占用，未解决解密；本机该直接读取路线不可用 | 改用用户本机导出的 YouTube Cookie；不禁用浏览器加密 |
| 没有 Firefox Cookie/profile | 本机未配置该登录环境 | 不把 `--cookies-from-browser firefox` 当已可运行的解决方案 |
| 浏览器能播放，下载器仍无法访问会员视频 | 下载器的认证会话、客户端或依赖可能不同 | 先核对单站导出、有效会员权限、版本；不立即判为没买会员 |
| 缺 EJS、JS 挑战处理不可用 | 仅安装 yt-dlp 或仅有 Node 可能不足 | 核对匹配的 yt-dlp-ejs，并显式传入 Node 路径 |
| 默认播放器路线出现 `tv_downgraded` / `page_reload` 类错误 | 本机默认路线失败；补齐依赖后另一组合成功 | 使用已验证的 `web_safari,web_creator`；它是本机基准，不是永远适用的设置 |
| Cookie 文件解析失败 | 错格式、空文件或不完整导出 | 不打印解析器原文；让用户重新导出 Netscape 单站文件 |
| 再次显示需登录 / 需会员 | Cookie 过期、轮换或账号权限变化均需核对 | 停止依赖此凭据的批次，让用户更新登录状态或确认权限 |
| 格式缺失、403、PO Token 提示 | YouTube 当时的客户端/请求要求可能变化 | 查官方当前说明，保留失败；本文没有验证或部署 PO Token 方案 |
| 浏览器自动化工具不能确认外层 URL | 属于该工具的界面识别限制 | 停止受阻的 UI 路线；不要修改或绕过安全限制；本机成功下载使用独立本地导出与下载器 |
| 已下载、中文文件路径在日志读取时异常 | Python 子进程文本解码可能使用 GBK | 指定 UTF-8；先核对媒体，不据此重下载整场 |

不因一次失败就新增账号、改 Google 安全配置、反复重启所有浏览器或自动安装不明插件。需要用户操作时明确说缺的是“单站登录导出”还是“访问权限确认”，不用索取密码。

## 8. 多视频作业与停止条件

- 冻结本次 ID、顺序、目标画质、输出目录。先试一个，再顺序处理剩余指定 ID，场次间留约 5–10 秒。
- 本机成功批次为单任务、单分片并发；重试和分片重试最多 2 次，提取重试为 0。不要自动扩大并发或无限重试。
- 有明确完成条件、截止时间和可中断进程。记录当前 ID、已完成/失败数、更新时刻；状态文件不要带请求头、Cookie、原始媒体 URL。
- 续跑先检查已验收文件及哈希；未验收的暂存媒体重新核对。保留 `.part` 供续传，不自动清理历史下载。
- 遇到登录验证、权限不足、认证文件不合格、持续限流或无法确认下载授权，停止相关作业并报告，不将失败场次补造成完成。
- 下载授权不等于批量转录/事件提取授权；本项目后续研究仍须遵循用户最新 Planning / 执行指令和试点验收条件。

## 9. 发布给其他 AI 的范围

GitHub 可以保存此方法文档及不含凭据的通用示例。**不要提交实际 Cookie 文件、密码、认证数据库、完整下载器返回对象、带签名 URL、会员视频、原逐字稿或截图**。本机任务日志、清单和证据如需备份，只按当前任务授权与明确文件白名单发送到私人云盘。

接手 AI 可以复述方法、检查现有环境和指定任务状态，但不从公开文档反推授权、不搜索无关目录、不把认证材料交给其他 agent。

## 10. 官方参考与更新入口

- [yt-dlp：YouTube Cookie 导出与常见错误](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies)
- [yt-dlp：EJS、运行时与版本要求](https://github.com/yt-dlp/yt-dlp/wiki/EJS)
- [yt-dlp：Cookie FAQ](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp)
- [Get cookies.txt LOCALLY 源码](https://github.com/kairi003/Get-cookies.txt-LOCALLY)

这些入口用于检查接口或依赖变化，不能覆盖本文已记录的实测事实，也不能作为扩展用户授权的依据。

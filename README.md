# Wallpaper Changer

跨平台**自动更换桌面壁纸**应用：Python + Qt（PySide6）开发，支持 **Ubuntu 26.04 / Linux** 与 **Windows**。
图片可来自 **Bing 每日壁纸**、**本地文件夹**或**自定义链接**（直链 / JSON 接口）；具备收藏、历史回溯、系统托盘常驻与开机自启动。

---

## 一、功能一览

| 能力 | 说明 |
| --- | --- |
| 图片来源 | Bing 每日图（官方 `HPImageArchive` 接口，免 Key）、**Bzee 图库**（photo.bzee.cn，桌面/手机图库 · 分类 · 关键词）、本地文件夹（多目录、递归、自动读分辨率）、自定义链接（直链 / 本地路径 / JSON 接口） |
| 定时轮换 | 分钟/小时级间隔、随机或顺序、N 天内不重复、最小分辨率过滤、启动即换一张；**启动即开始倒计时**，每次换图成功后重新计时，勾选「启用自动轮换」即时生效 |
| 显示方式 | 填充 / 适应 / 拉伸 / 居中 / 平铺 / 跨屏延展（自动映射为各平台取值） |
| 收藏 | 一键收藏、收藏夹网格视图、可只轮换收藏、右键另存到本地 |
| 历史 | 完整应用记录、随时回到上一张、搜索与筛选、单条删除；启动时首页回显「当前壁纸」 |
| 托盘 | 托盘常驻、双击开界面、右键菜单直接换图/收藏/暂停/退出 |
| 开机启动 | Linux 写 `~/.config/autostart/*.desktop`（或可选 systemd --user）；Windows 写 `HKCU\...\Run` |
| 其他 | 单实例、失败/错误通知（成功静默）、Light/Dark 主题、HTTP 代理、缓存上限清理、日志 |

---

## 二、快速开始

### Linux（Ubuntu 26.04）

```bash
# 安装依赖（pip 与 apt 二选一，见下方说明）
python3 -m pip install --break-system-packages -r requirements.txt

python main.py                            # 启动界面
python main.py --minimized                # 只驻留托盘
python main.py --doctor                   # 环境自检（推荐先跑一次）
```

> 两种装法都行，选一种即可：
>
> - **pip（推荐，不依赖发行版仓库）**：`python3 -m pip install --break-system-packages -r requirements.txt`
>   （Ubuntu 23.04+ 因 PEP 668 需要 `--break-system-packages`；若提示 pip 不存在，先 `sudo apt install python3-pip`）
> - **apt（需启用 universe）**：`sudo add-apt-repository universe && sudo apt update && sudo apt install python3-pyside6.qtwidgets python3-pyside6.qtnetwork python3-requests python3-pil`
>
> apt 装的 PySide6 与 pip 装的互不可见：用 apt 装就统一走 apt，用 pip 装就统一走 pip，不要混用。
> 装完可用 `python3 main.py --doctor` 复核环境。

### Windows

```bat
pip install -r requirements.txt
python main.py
```

### 运行截图

![主界面](docs/screenshot-main.png)

---

## 三、目录结构

```
wallpaper-changer/
├── main.py                      桌面入口
├── wallpaper_changer/
│   ├── app.py                   应用核心：CLI、单实例、主题、日志
│   ├── config.py                配置模型与 JSON 持久化
│   ├── database.py              SQLite：图片库 / 收藏 / 历史
│   ├── engine.py                轮换引擎（Qt 信号 + 后台线程）
│   ├── fetcher.py               下载、缓存、缩略图、导出
│   ├── wallpaper.py             跨平台设置壁纸（Linux/Windows）
│   ├── autostart.py             开机自启动（XDG / systemd / 注册表）
│   ├── models.py                ImageRecord 数据模型
│   ├── sources/                 来源插件
│   │   ├── base.py              抽象基类（Session、代理、超时）
│   │   ├── bing.py              Bing 每日壁纸
│   │   ├── local.py             本地文件夹
│   │   └── custom_urls.py       自定义链接
│   └── ui/                      Qt 界面
│       ├── main_window.py       主窗口（6 个配置页）
│       ├── gallery.py           缩略图网格（收藏/历史）
│       ├── tray.py              系统托盘
│       └── icons.py             图标
├── packaging/
│   ├── linux/                   .desktop / build-deb.sh / build-appimage.sh / requirements-appimage.txt
│   └── windows/                 PyInstaller spec / build.bat / installer.iss
├── scripts/
│   ├── gen_assets.py            生成 icon.png / icon.ico
│   └── screenshot.py            离屏渲染主界面，生成文档截图
├── tests/                       单元测试与冒烟测试
└── assets/                      icon.png / icon.ico
```

---

## 四、打包分发

### 1) Debian / Ubuntu（.deb）

```bash
# 轻量包（约 77K，依赖系统已有的 Python + PySide6）
bash packaging/linux/build-deb.sh 1.0.1
sudo apt install ./dist/wallpaper-changer_1.0.1_all.deb

# 自包含包（约 59M，内置 PySide6 / requests / Pillow，不依赖系统 Qt 包）
bash packaging/linux/build-deb.sh 1.0.1 --bundle
sudo apt install ./dist/wallpaper-changer_1.0.1_all.deb
```

产物包含 `/opt/wallpaper-changer`、`/usr/bin/wallpaper-changer` 启动器、菜单项与图标。
脚本会自动检测文件系统是否支持 `chmod`，不支持时改用临时目录构建。

#### 为什么不再硬依赖 `python3-pyside6.qtwidgets`

该包名位于 Ubuntu 的 **universe** 仓库，很多系统（未启用 universe、或 PySide6 由 pip 安装）
里它并不存在。若写进 `Depends`，软件安装器会直接报「依赖关系不满足」而拒绝安装——
即使你命令行里 `python main.py` 能正常跑（说明 PySide6 其实是 pip 装的）。

因此现在的策略：

| 位置 | 做法 |
| --- | --- |
| `Depends` | 只保留 `python3 (>= 3.9)`，保证 deb **一定能装** |
| `Recommends` | 构建时用 `apt-cache` 探测，只把系统里真实存在的包名写进去 |
| 启动器 | 逐项检查依赖，缺什么给什么命令（区分 apt / 两条路） |
| `--bundle` | 把依赖打进 `/opt/wallpaper-changer/vendor`，彻底摆脱系统包 |

启动器还会自动挑选能 `import PySide6` 的解释器（覆盖 `python3.9`~`python3.14` 多版本共存），
并把失败信息写入 `~/.local/share/wallpaper-changer/launcher.log`，有 `zenity`/`notify-send` 时弹窗提示。

安装后若有问题，先跑自检：

```bash
wallpaper-changer --doctor
```

它会逐项列出依赖、目录权限、图标与壁纸后端，并给出 apt 与 pip 两种修复命令。

### 2) AppImage（免安装、跨发行版）

```bash
bash packaging/linux/build-appimage.sh 1.0.0          # 出 .AppImage
bash packaging/linux/build-appimage.sh 1.0.0 --dir-only # 只产出 AppDir，便于自查
chmod +x dist/Wallpaper-Changer-1.0.0-x86_64.AppImage
./dist/Wallpaper-Changer-1.0.0-x86_64.AppImage --show
```

AppImage 内自带 Python 标准库与依赖，不依赖目标机 Python 版本；构建机会自动下载 `appimagetool`（也可手动放到 `build/appimagetool`）。

### 3) Windows 可执行程序与安装器

在项目根目录执行（需 Python 3.9+ 且已加入 PATH）：

```bat
REM 批处理版
packaging\windows\build.bat              REM 默认 1.1.3
packaging\windows\build.bat 1.1.3 one    REM 第二个参数 one = 单文件 exe
```

```powershell
# PowerShell 版（推荐：中文不乱码、可传参）
.\packaging\windows\build.ps1
.\packaging\windows\build.ps1 -Version 1.1.3 -OneFile
.\packaging\windows\build.ps1 -Console          # 带控制台窗口，排错用
.\packaging\windows\build.ps1 -SkipInstaller    # 只出程序目录
```

脚本会自动完成：检查 Python/依赖 → 自动装 `requirements.txt` 与 PyInstaller → 清理旧产物 →
PyInstaller 打包 → 检测到 Inno Setup 6 时生成安装器。

产物：

| 产物 | 路径 |
| --- | --- |
| 程序目录（默认，推荐） | `dist\Wallpaper Changer\Wallpaper Changer.exe` |
| 单文件 exe（`-OneFile`） | `dist\Wallpaper Changer.exe` |
| 安装器 | `dist\WallpaperChanger-<版本>-Setup.exe` |

**为什么默认用目录版**：单文件版每次启动都要把 70+ MB 解压到临时目录，慢 2~5 秒，
且更容易被杀软误报。目录版启动快、误报低，配安装器或压缩包分发都合适。

安装器（Inno Setup 6）提供：开始菜单/可选桌面快捷方式、**开机自启（最小化到托盘）**、
安装到 `%LOCALAPPDATA%\Programs`（免管理员、不弹 UAC）、卸载时自动结束进程、
并**询问是否保留配置与历史**（默认保留，方便重装后继续用）。
未安装 Inno Setup 时会跳过安装器，程序目录仍可直接分发（绿色版）。

> 依赖：`pip install -r requirements.txt pyinstaller`；
> 安装器需 [Inno Setup 6](https://jrsoftware.org/isdl.php)。

**常见问题**

| 现象 | 处理 |
| --- | --- |
| 双击 exe 无反应/闪退 | 用 `-Console` 重新打包，能看到报错；或命令行运行看输出 |
| 托盘/界面图标不显示 | 已通过 `sys._MEIPASS` 定位 `assets/`，打包时随 `datas` 带上；`--doctor` 会检查图标文件是否存在 |
| webp 图片无法解码 | spec 已显式带上 `PIL._webp` 与 `libwebp*.so/dll`，勿自行裁剪 Pillow |
| 杀软报毒 | 换目录版、或代码签名；PyInstaller 打包的程序误报较常见 |
| 开机自启不生效 | 安装器写的是「启动」文件夹快捷方式（带 `--minimized`），可在任务管理器「启动」页确认 |

---

## 五、配置说明

| 页签 | 关键项 |
| --- | --- |
| 轮换 | 开关、间隔（分钟/小时）、启动即换、随机顺序、N 天不重复、显示方式、最小分辨率、网络超时 |
| 图片来源 | Bing（区域市场、天数、UHD/1080p）、本地（目录列表、递归）、自定义 URL |
| 图库 | 收藏与历史合并为单一图库，顶栏可切换「历史 / 收藏 / 全部」；缩略图三档尺寸，**始终按原始宽高比完整显示、绝不裁切变形**；双击查看完整大图，右键菜单（设为壁纸/收藏/另存/打开原链接/删除），工具栏可一键重建缩略图 |
| 高级 | 开机自启、最小化启动、关闭到托盘、出错时通知、systemd 方式、缓存目录与上限、代理、主题、日志级别 |
| 维护 | 清空历史记录、恢复默认设置 |

配置文件位置：

- Linux：`~/.config/wallpaper-changer/config.json`
- Windows：`%APPDATA%\WallpaperChanger\config.json`
- 数据（壁纸缓存、缩略图、SQLite、日志）：`~/.local/share/wallpaper-changer/` 或 `%APPDATA%\WallpaperChanger\`

### 自定义链接的三种写法

```
https://example.com/a.jpg                          # 图片直链
/home/user/Pictures/a.png                          # 本地文件
json:https://example.com/api/wallpapers            # JSON 接口，自动提取图片链接
json:https://example.com/api/wallpapers#data.list  # 指定字段路径（点号分隔）
```

JSON 接口会自动识别 `url / image / src / download_url` 等字段以及嵌套的列表结构。

---

## 六、命令行用法

```bash
python main.py --show        # 呼出已运行实例的界面
python main.py --next        # 立即换一张
python main.py --prev        # 回到上一张
python main.py --refresh     # 重新抓取来源图池
python main.py --quit        # 退出运行中的实例
python main.py --minimized   # 启动后只驻留托盘
python main.py --log-level DEBUG
```

适合配合系统定时器或快捷键使用，例如 GNOME 自定义快捷键绑定 `python /path/main.py --next`。

---

## 七、桌面环境适配

| 环境 | 方式 |
| --- | --- |
| GNOME / Ubuntu（含 Wayland） | `gsettings` 设置 `picture-uri` 与 `picture-uri-dark` |
| KDE Plasma | `qdbus6/qdbus/dbus-send` 调用 PlasmaShell，或 `plasma-apply-wallpaperimage` |
| XFCE | `xfconf-query` 遍历所有 backdrop |
| MATE / Cinnamon / Deepin | 各自 gsettings schema |
| Sway / 其他 | `swaybg`；X11 下还可兜底 `feh` |
| Windows | `SystemParametersInfoW` + 注册表 `WallpaperStyle`（自动转 BMP） |

> 当前会话以 Wayland + GNOME 为主；若检测到环境不明，会按 GNOME → feh → swaybg 顺序尝试。

---

## 八、测试

```bash
python -m pytest tests -q        # 核心逻辑：配置、数据库、来源、缓存、Linux 命令拼装
python tests/test_ui_smoke.py    # 界面与引擎冒烟（offscreen，无需真实桌面）
```

核心测试 13 项，覆盖配置读写往返、数据库收藏/历史/去重/清理、本地目录扫描与分辨率读取、
尺寸过滤、缓存落盘与清理、自定义链接（含 JSON 接口解析）、Linux `gsettings` 命令拼装、缺失文件处理。

冒烟测试覆盖：本地来源抓取 → 候选池生成 → 切换与失败重试 → 收藏/历史 → 六个配置页切换 → 图标加载。

---

### 双击的完整大图 与「显示方式」

双击缩略图会弹出**完整大图预览**（按屏幕可用面积等比缩放，不变形、不裁切），
**不会**直接把图片设为壁纸；要设为壁纸请用右键菜单「设为当前壁纸」。

真正决定桌面上图片是否被裁切的是「轮换 → 显示方式」：

| 取值 | 桌面效果 |
| --- | --- |
| **适应（完整显示）**（默认） | 整张图完整可见，比例正确，可能有留白 |
| 填充（缩放裁切） | 铺满屏幕，但**会裁掉多余部分** |
| 拉伸 | 强制铺满，比例失真 |

新配置默认为「适应（完整显示）」。若你的配置是早期版本生成的、仍是「填充（缩放裁切）」，
请在「轮换 → 显示方式」里改一次并保存即可。

### Bzee 图库（photo.bzee.cn）

站点未提供公开 API，因此按**页面抓取**方式接入：解析分类页 HTML 里的图片条目。

**界面配置**（图片来源 → Bzee 图库）：

| 配置项 | 说明 |
| --- | --- |
| 启用 | 默认关闭，勾选后才生效 |
| 图库 | 桌面图库 `desktop` / 手机图库 `mobile` |
| 分类 | 多选；**不勾任何分类 = 抓该图库首页的全部** |
| 关键词 | 逗号分隔，对标题做包含匹配（如 `风景,星空`） |
| 页数 | 1~5 页，默认 1 |
| 尺寸 | `origin` 原图（默认）／ `thumb` 缩略图 |

**URL 规则**（解析所得）：

```
分类页：  https://photo.bzee.cn/desktop/{slug}     https://photo.bzee.cn/mobile/{slug}
缩略图：  https://imgtu.bzee.cn/photo/thumb/{大类}/{子类}/{ID}_{宽}x{高}.webp
原  图：  https://imgtu.bzee.cn/photo/pic/{大类}/{子类}/{ID}.webp
          即 thumb -> pic，并去掉 _{宽}x{高} 后缀
```

> ⚠️ 原图目录是 **`pic`**，不是 `photo` 根目录下的直接路径。
> 早期版本曾写成 `/photo/{大类}/...`，结果只能拿到缩略图（画面糊），
> 现已修正为 `/photo/pic/...`；代码里也保留了对旧写法的自动纠偏。

**稳健性设计**（这点很重要）：

- 分类 slug **不硬编码依赖**：内置一份拼音 slug 表（桌面 13 类、手机 6 类），
  界面上有「在线刷新分类」按钮，会抓首页解析真实链接来替换内置表。
  若站点改版导致内置表失效，点一次即可恢复。
- **原图失败自动回退缩略图**：`remote_url` 取原图，`fallback_url` 存缩略图，
  下载时先试原图、失败再试缩略图，不会因单张图 404 而中断。
- **403 不抛异常**：站点有 WAF，某些网络出口会被拒。此时仅记录错误文本
  （「站点拒绝了请求（403）」），主流程继续走 Bing / 本地等其它来源。
- 结果按图片 ID 去重，并按配置上限截断。

> 爬取频率请自行节制：页数上限设为 5，默认只抓 1 页。

---

## 九、扩展新的图片来源

在 `wallpaper_changer/sources/` 新建模块，继承 `Source`：

```python
from .base import Source
from ..models import FetchResult, ImageRecord

class MySource(Source):
    name = "my"
    label = "我的图源"
    needs_network = True

    def fetch(self, limit: int = 30) -> FetchResult:
        res = FetchResult(source=self.name)
        # ... 请求接口，构造 ImageRecord(source=self.name, source_id=..., remote_url=...)
        return res
```

然后在 `sources/__init__.py` 的 `REGISTRY` 与 `build_sources()` 中注册，并在 `config.py` 增加对应设置段与界面控件即可。

---

## 十、许可与版权

代码可自由使用与修改。图片资源版权归各自作者所有，Bing 图片仅供个人欣赏、请勿用于商业用途。
使用本地图片或自定义链接时，请自行确认拥有相应授权。

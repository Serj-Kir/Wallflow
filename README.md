<div align="center">

# Wallflow

**Бесплатный движок живых обоев для Linux и Windows.**

Видео, HTML и анимированные сцены на рабочем столе. Плейлисты, профили,
расписание по времени и обои на каждом мониторе — без Steam и без подписки.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Platforms](https://img.shields.io/badge/platforms-Linux%20%7C%20Windows%20%7C%20macOS-lightgrey)

</div>

---

## Что умеет

| | |
|---|---|
| **Видео-обои** | mp4, webm, mkv, avi — зациклены бесконечно |
| **HTML-обои** | HTML/CSS/JS через встроенный Chromium |
| **Сцены** | 8 анимированных сцен, JSON, без внешних зависимостей |
| **Фото** | jpg, png, webp, gif — с поддержкой тёмной темы рабочего стола |
| **Плейлисты** | порядок или случайно, интервал, переходы |
| **Профили** | именованные наборы обоев, свой профиль на каждый монитор |
| **Расписание** | правила вида «в 09:00–18:00 в будни — такой профиль» |
| **Пауза** | полный экран, батарея, блокировка, простой, удалённый сеанс |
| **Горячие клавиши** | глобальные (Windows, X11) |
| **Источники** | 8 бесплатных сайтов, 5 работают без ключа |

---

## C# / Avalonia port

A cross-platform C# application is being developed alongside the original Python version:

```powershell
dotnet run --project .\src\Wallflow.App\Wallflow.App.csproj
```

Run the migrated-core tests with:

```powershell
dotnet test .\src\Wallflow.App.Tests\Wallflow.App.Tests.csproj
```

The C# version currently includes an Avalonia desktop shell; persistent wallpaper library,
search, favourites, folder scanning and import; profile, playlist, and schedule management;
online wallpaper providers; settings and system autostart; pause policies; static wallpaper
application on Windows, macOS, and selected Linux desktops; and animated scene previews. It
also has live image, scene, video, and HTML renderers, per-monitor wallpaper surfaces, a system
tray, and global hotkeys on Windows and X11.

The desktop backends support the accent colour used by the running session: Windows writes the
DWM colourisation value, Sway applies it through `swaymsg`, and KDE Plasma through
`evaluateScript`; GNOME does not support it. The Windows backend also honours the configured fit
mode when it applies a static image, persisting `WallpaperStyle` / `TileWallpaper` in the
registry.

In-window shortcuts mirror the Python application: `Ctrl+Space` toggles playback, `Ctrl+←` /
`Ctrl+→` step through the playlist, `Ctrl+1` … `Ctrl+6` switch between the pages, and `Ctrl+Q`
quits. The page-switching and quit shortcuts are handled locally; the global ones are registered
by `GlobalHotkeyManager`. The C# configuration and library files use the
same JSON names as the Python version. Its settings page includes automatic-pause thresholds,
startup/tray behavior, video looping and audio, and HTML mouse interaction. Per-monitor image
assignments support fill, fit, stretch, center, and tile modes with alignment controls.
The playlist UI supports ordered contents, shuffle, intervals, and no-transition/fade options.
Schedule rules can be edited by weekday, time range, enabled state, and priority.
Profiles support duplication (including per-monitor assignments and accent color),
renaming, selecting a playlist, and choosing a startup profile.
The library view can filter by wallpaper type and favourites, and edit names, authors,
descriptions, and tags without modifying the source files.
Static images can use the native desktop wallpaper API, an application surface, or automatic
selection; explicit per-monitor assignments continue to use application surfaces. Scene rendering
also observes the configured maximum frame rate.
Animated GIFs are decoded and played on live wallpaper surfaces.
On GNOME and Cinnamon Wayland sessions, static wallpapers use the desktop's GSettings schema
(with a dconf fallback), including GNOME's dark-mode image and supported fit modes. Live wallpaper
surfaces and per-monitor desktop placement are not available on these compositors through ordinary
application windows; this Wayland integration still needs a GUI smoke test on Linux.

The C# app supports `--version`, `--help`, `--data-dir`, `--set`, `--profile`,
`--list-monitors`, `--reset-settings`, `--minimized` / `--hidden`, `--verbose`, and `--quit`.
`--quit` sends a local per-user request to the running instance. The `--set` command keeps
animated wallpapers running until the app is closed; static images are applied through the
desktop wallpaper API and the command then exits.

This is still an incremental port, not a verified replacement for the Python application.
Desktop-layer placement, multi-monitor behavior, tray integration, global shortcuts, and
video/HTML playback have not yet had a full GUI smoke test on the supported desktop platforms.
Windows and macOS video runtimes are included by the application packages; Linux video playback
requires a compatible system LibVLC installation. HTML playback uses the operating system's
native web-view engine (WebView2 on Windows, WKWebView on macOS, and WebKitGTK/WPE on Linux),
which may need to be installed separately. Linux desktop-layer behavior varies by compositor;
Wayland support is especially limited and is not guaranteed. Keep the Python implementation for
workflows that require verified platform behavior until the C# port has been tested on the
target desktop.

The video-upscaling notice that the Python version shows when a video is smaller than its
monitor is not implemented yet. Everything else listed above is implemented, but the
interactive behaviour still needs the GUI smoke tests that the Python side received.

---

## Установка

### Linux

```bash
git clone https://github.com/Serj-Kir/Wallflow.git
cd Wallflow

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/python -m wallflow
```

Нужна системная библиотека для Qt (Ubuntu / Mint / Debian):

```bash
sudo apt install libxcb-cursor0
```

### Windows

```powershell
git clone https://github.com/Serj-Kir/Wallflow.git
cd Wallflow

python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

.venv\Scripts\python -m wallflow
```

### Автозапуск

Включается одной галочкой в **Настройки → Общие**. Или вручную:

```bash
python -m wallflow --set ~/Videos/wallpaper.mp4   # применяет и держит
```

---

## Поддержка систем

Статичные фото всегда идут через штатный API рабочего стола — это самое чёткое
качество и правильное поведение при блокировке экрана. Анимированные обои и
разные обои на каждом мониторе рисует сам Wallflow.

| Система | Фото | Видео / HTML | Свои обои на каждый монитор |
|---|:--:|:--:|:--:|
| **GNOME, Cinnamon, MATE, Unity** (X11) | ✅ `gsettings` | ✅ своё окно | ✅ своё окно |
| **GNOME / Cinnamon** (Wayland) | ✅ | ❌ | ❌ |
| **KDE Plasma** | ✅ | ✅ | ✅ `qdbus` |
| **XFCE** | ✅ | ✅ | ✅ `xfconf-query` |
| **Sway** | ✅ | ✅ | ✅ `swaymsg` |
| **Hyprland** | ✅ | ✅ | ✅ `hyprctl` |
| **LXQt** | ✅ | ✅ | ✅ |
| **Любой X11** | ✅ `feh` | ✅ | ❌ |
| **Windows** | ✅ `SystemParametersInfoW` | ✅ | ✅ `EnumDisplayMonitors` |
| **macOS** | ✅ | ✅ | ❌ |

> **Про Wayland.** Композитор управляет порядком окон сам и игнорирует
> `_NET_WM_WINDOW_TYPE_DESKTOP`, поэтому приложение не может оказаться под
> значками рабочего стола. На чистом Wayland доступны только статичные фото.
> Под XWayland анимация работает. На KDE, Sway и Hyprland — полная поддержка,
> им Wayland не мешает.

Проверено вживую на **GNOME/Cinnamon под X11**. Остальные бэкенды написаны по
документации соответствующих систем, но на этой машине не проверялись.

---

## Форматы обоев

### Видео
Обычный файл: `.mp4`, `.webm`, `.mkv`, `.avi`, `.mov`. Зацикливается
бесконечно. Если разрешение ролика меньше монитора, Wallflow предупредит и
покажет во сколько раз картинка растянется.

### HTML
Папка с `index.html` и любыми своими файлами. Необязательный `project.json`:

```json
{
  "title": "Пульс",
  "author": "ваше имя",
  "description": "Живая сеть частиц",
  "preview": "preview.jpg",
  "background": "#060814"
}
```

Странице доступен `window.wallflow.onPause(fn)`, чтобы останавливать тяжёлую
работу, когда обои не показываются.

### Сцена
JSON-файл `.scene` — лёгкая альтернатива видео без нагрузки на процессор:

```json
{
  "type": "particles",
  "fps": 30,
  "background": {"type": "gradient", "colors": ["#0b1020", "#131a33"]},
  "params": {"count": 140, "hue": 210, "link_distance": 130}
}
```

Типы: `particles`, `starfield`, `waves`, `rain`, `bubbles`, `plasma`,
`breathing`, `grid`. Неизвестные поля игнорируются — битый файл не уронит
воспроизведение.

---

## Источники

Работают без ключа: **Bing**, **Openverse**, **LoremFlickr**, **Lorem Picsum**,
**NASA APOD**. С бесплатным ключом: **Unsplash**, **Pixabay**, **Pexels**.

Ключи вводятся в **Настройки → Из интернета → «Ключи API…»** и хранятся только
у вас. Задать через переменные окружения:

```bash
export WALLFLOW_UNSPLASH_KEY=...
export WALLFLOW_PIXABAY_KEY=...
export WALLFLOW_PEXELS_KEY=...
```

---

## Ключи и горячие клавиши

| Действие | По умолчанию |
|---|---|
| Пауза / воспроизведение | `Ctrl+Alt+P` |
| Следующие обои | `Ctrl+Alt+→` |
| Предыдущие обои | `Ctrl+Alt+←` |
| Открыть Wallflow | `Ctrl+Alt+W` |

Глобальные клавиши работают на Windows и X11. Под Wayland композитор не даёт
приложениям перехватывать клавиши — внутри окна работают `Ctrl+Space`,
`Ctrl+←` / `Ctrl+→` и `Ctrl+1`…`Ctrl+6`.

---

## Команды

```
--set ФАЙЛ        применить обои и держать (папка — HTML, .scene — сцена)
--list-monitors   показать мониторы и возможности системы
--profile ИМЯ     стартовать с указанным профилем
--minimized       запустить свёрнутым в трей
--data-dir ПУТЬ   своя папка с данными (для портативного режима)
--reset-settings  сбросить настройки
--quit            закрыть запущенный экземпляр
-v                подробный журнал
```

Пример — узнать, что умечивает текущая система:

```bash
python -m wallflow --list-monitors
```

```
Система: GNOME / Cinnamon / MATE  (gnome)
  свои обои на каждый монитор: нет
  сессия: X11
Мониторы:
  1. DVI-D-1  1680x1050  @(0,0)  основной
```

---

## Где лежат данные

| Система | Путь |
|---|---|
| Linux | `~/.local/share/wallflow/` |
| Windows | `%APPDATA%\wallflow\` |

Там `config.json`, `library.json`, обои, превью и журнал. Все настройки
сохраняются атомарно, с ротацией резервных копий в `backups/`.

Чтобы полностью снести приложение — удалите эту папку.

---

## Разработка

```bash
.venv/bin/python tools/smoketest.py           # 83 проверки, без дисплея
.venv/bin/python tools/make_demo.py          # демо-обои в библиотеку
```

Диагностика на живой системе:

```bash
python tools/diagnose_video.py ФАЙЛ.mp4     # декодирование и кадры
python tools/diagnose_window.py ФАЙЛ.mp4    # окно обоев целиком
python tools/check_video_loop.py ФАЙЛ.mp4   # зацикливается ли видео
python tools/check_video_quality.py ФАЙЛ.mp4
python tools/check_animation.py a.png b.png # отличаются ли два кадра
```

### Структура

```
wallflow/
  engine/    работа с системой: GNOME, KDE, Sway, Windows, питание, мониторы
  live/      рендеринг: сцены, видео, HTML, фабрика виджетов
  ui/        интерфейс: библиотека, плейлисты, профили, расписание, настройки
  models.py  датаклассы: обои, профили, плейлисты, правила, мониторы
  config.py  настройки с атомарной записью и бэкапами
  library.py индексация библиотеки и превью
  sources.py бесплатные источники изображений
  player.py  окна обоев, переходы, режим native/window
  window.py  окно уровня рабочего стола (X11 / Windows)
  hotkeys.py глобальные горячие клавиши
```

---

## Ограничения

- Разрешение видео важнее кодека: ролик 640×360 на мониторе 1920×1080 будет
  размытым, и никакой фильтр это не исправит. Wallflow предупредит заранее.
- Под Wayland без XWayland анимация невозможна — см. таблицу выше.
- Видео на сцене декодируется программно и на 4K может нагружать процессор;
  ограничение в настройках «Максимум кадров в секунду».

---

## Лицензия

MIT — см. [LICENSE](LICENSE).
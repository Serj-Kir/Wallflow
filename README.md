<div align="center">

# Wallflow

**Бесплатный движок живых обоев для Windows и Linux.**

Видео, HTML и анимированные сцены на рабочем столе. Плейлисты, профили,
расписание по времени и обои на каждом мониторе — без Steam и без подписки.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![.NET](https://img.shields.io/badge/.NET-10-512BD4)
![Platforms](https://img.shields.io/badge/platforms-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)

</div>

---

## Что умеет

| | |
|---|---|
| **Видео-обои** | mp4, webm, mkv, avi и другие — зациклены бесконечно (LibVLC) |
| **HTML-обои** | HTML/CSS/JS через встроенный WebView, с мостом `window.wallflow` |
| **Сцены** | 9 анимированных сцен (`particles`, `starfield`, `waves`, `rain`, `bubbles`, `gradient`, `plasma`, `breathing`, `grid`) прямо из `.scene` JSON |
| **Фото** | jpg, png, webp, gif (с анимацией) — режимы вписывания fill/fit/stretch/center/tile |
| **Плейлисты** | порядок или случайно, интервал, длительность перехода |
| **Профили** | именованные наборы обоев, обои на каждый монитор, акцентный цвет |
| **Расписание** | правила вида «в 09:00–18:00 в будни — такой профиль» |
| **Пауза** | полный экран, батарея, блокировка, простой, удалённый сеанс |
| **Горячие клавиши** | глобальные Ctrl+Alt+P / Ctrl+Alt+←/→ / Ctrl+Alt+W, локальные Ctrl+1…6, Ctrl+Space |
| **Библиотека** | сканирование, миниатюры, поиск, избранное, теги, свойства, удаление |
| **Источники** | Bing, Openverse, LoremFlickr, Picsum, NASA APOD, Unsplash, Pixabay, Pexels |
| **Акцент** | автоматический подбор цвета рабочего стола из текущих обоев |
| **Трей и автозапуск** | сворачивание в трей, старт с системой, один экземпляр приложения |

Проект целиком написан на C# (Avalonia 12, .NET 10). Python-реализации в
репозитории больше нет — всё переведено на этот стек.

---

## Требования

| Платформа | Что нужно |
|---|---|
| Windows 10/11 | .NET 10 SDK (для сборки) или self-contained публикация; WebView2 Runtime |
| Linux | .NET 10 SDK, `libvlc` (например `libvlc-dev` / `vlc`), GTK/WebKitGTK для HTML-обоев |
| macOS | .NET 10 SDK; `.dylib`-комплект VLC ставится NuGet-пакетом |

Зависимости на Linux: видео — системный `libvlc.so` (пакет `vlc` или `libvlc`),
HTML — `libwebkit2gtk` (Avalonia WebView).

---

## Сборка и запуск

```bash
git clone https://github.com/Serj-Kir/Wallflow
cd Wallflow

# Проверка сборки решения
dotnet build Wallflow.slnx

# Запуск
dotnet run --project src/Wallflow.App

# Тесты
dotnet test src/Wallflow.App.Tests
```

Публикация отдельной сборки:

```bash
dotnet publish src/Wallflow.App -c Release -r win-x64 --self-contained false
dotnet publish src/Wallflow.App -c Release -r linux-x64 --self-contained false
dotnet publish src/Wallflow.App -c Release -r osx-arm64 --self-contained false
```

---

## Командная строка

| Флаг | Описание |
|---|---|
| `--set PATH` | применить файл, папку с `index.html` или `.scene` сразу и выйти при статике |
| `--profile NAME` | запустить с указанным профилем |
| `--list-monitors` | вывести бэкенд, поддержку мультимонитора, акцента и список мониторов |
| `--minimized`, `--hidden` | запустить свёрнутым в трей |
| `--data-dir PATH` | переопределить папку данных (то же, что `WALLFLOW_DATA_DIR`) |
| `--reset-settings` | сбросить настройки до значений по умолчанию |
| `--quit` | закрыть уже запущенный Wallflow |
| `-v`, `--verbose` | подробный журнал (пишется в `logs/wallflow.log`) |
| `--version`, `--help` | версия и справка |

---

## Где хранятся данные

| ОС | Данные | Кэш |
|---|---|---|
| Windows | `%APPDATA%\wallflow` | `%LOCALAPPDATA%\wallflow` |
| Linux | `$XDG_DATA_HOME/wallflow` (иначе `~/.local/share/wallflow`) | `$XDG_CACHE_HOME/wallflow` |
| macOS | `~/Library/Application Support/wallflow` | `~/Library/Caches/wallflow` |

Внутри папки данных: `config.json` (+ `backups/`), `library.json`,
`profiles.json`, `playlists.json`, `rules.json`, `wallpapers/`, `thumbs/`,
`logs/wallflow.log` (ротация 2 МБ × 3 файла).

Настройки лежат в `config.json` в формате snake_case; неизвестные ключи
сохраняются. Запись атомарная, при повреждении файла восстанавливается
последняя резервная копия.

---

## Структура проекта

```
Wallflow.slnx                       решение (.NET 10)
src/Wallflow.App/
  Program.cs                        точка входа, разбор CLI
  MainWindow.axaml(.cs)             интерфейс: библиотека, источники, плейлисты,
                                    профили, расписание, настройки
  Core/
    WallflowPaths.cs                папки данных и кэша
    WallflowConfig.cs               настройки, резервные копии, секции
    Wallpaper.cs, WallflowModels.cs модели
    WallpaperLibrary.cs             индекс, сканирование, импорт
    ThumbnailCache.cs               миниатюры (SkiaSharp, дисковый кэш)
    AccentColor.cs                  доминирующий цвет обоев
    MediaInfo.cs, VideoRuntime.cs   разрешение фото/видео, общий LibVLC
    Log.cs                          журнал с ротацией
    PlaybackController.cs           профили, плейлисты, расписание, паузы
    LiveWallpaperControl.cs         рендеры: фото/GIF/сцена/видео/HTML
    WallpaperSurfaceWindow.cs       окно-слой на монитор
    WallpaperBackend.cs             системные бэкенды (Windows/Linux/macOS)
    GlobalHotkeyManager.cs          глобальные клавиши
    WallflowTrayService.cs          трей
    AutostartService.cs             автозапуск и один экземпляр
    Sources/WallpaperSources.cs     8 онлайн-источников
    Scenes/                         сцены: спецификация, симулятор, отрисовка
src/Wallflow.App.Tests/             MSTest: ядро, модели, бэкенды, CLI, сцены
```

---

## Особенности платформ

- **Windows** — живые обои рисуются окном, вложенным в `WorkerW` (как в
  оригинальном движке), статические ставятся через `SystemParametersInfo`;
  глобальные клавиши через `RegisterHotKey`.
- **Linux** — статические обои ставятся через `gsettings`/`dconf` (GNOME,
  Cinnamon), `plasma-apply-wallpaperimage` (KDE), `xfconf-query` (XFCE),
  `swaymsg` (Sway), `hyprctl` (Hyprland), `pcmanfm-qt` (LXQt), `feh`/`xsetroot`
  (X11). Живые обои — окно на каждый монитор; на чистом Wayland без XWayland
  окно-слой поставить нельзя, об этом пишется в статус и журнал.
- **macOS** — статические обои через `System Events`, живые — окно нижнего
  уровня через Objective-C runtime.

---

## Лицензия

MIT — см. [LICENSE](LICENSE).

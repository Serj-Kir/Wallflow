using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Diagnostics;
using System.Text.Json;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Input;
using Avalonia.Input.Platform;
using Avalonia.Interactivity;
using Avalonia.Platform;
using Avalonia.Platform.Storage;
using Wallflow.App.Core;
using Wallflow.App.Core.Scenes;
using Wallflow.App.Core.Sources;

namespace Wallflow.App;

public partial class MainWindow : Window
{
    private readonly WallflowConfig _config;
    private readonly WallpaperLibrary _library;
    private readonly DesktopWallpaperPlayback _playback;
    private readonly PlaybackController _controller;
    private readonly GlobalHotkeyManager _hotkeys;
    private readonly WallflowTrayService? _tray;
    private readonly ObservableCollection<Wallpaper> _visibleWallpapers = [];
    private readonly ObservableCollection<SearchResult> _sourceResults = [];
    private readonly ObservableCollection<string> _libraryDirectories = [];
    private ThumbnailCache _thumbnails;
    private CancellationTokenSource? _sourceSearchCancellation;
    private IReadOnlyList<Provider> _providers = [];
    private bool _allowExit;
    private bool _loadingProfileMonitorSettings;
    private bool _loadingSettings;

    public MainWindow()
    {
        InitializeComponent();
        _config = new WallflowConfig();
        _library = new WallpaperLibrary(_config);
        _thumbnails = new ThumbnailCache(_config.Get("thumb_size", 220));
        _playback = new DesktopWallpaperPlayback(config: _config);
        _controller = new PlaybackController(_config, _library, _playback);
        WallpaperList.ItemsSource = _visibleWallpapers;
        ProfilesList.ItemsSource = _controller.Profiles;
        PlaylistsList.ItemsSource = _controller.Playlists;
        RulesList.ItemsSource = _controller.Rules;
        RuleProfileBox.ItemsSource = _controller.Profiles;
        ProfilePlaylistBox.ItemsSource = _controller.Playlists;
        StartupProfileBox.ItemsSource = _controller.Profiles;
        SourceResultsList.ItemsSource = _sourceResults;
        LibraryDirectoriesList.ItemsSource = _libraryDirectories;
        WallpaperKindFilterBox.SelectedIndex = 0;
        LoadSourceProviders();
        SourceProviderBox.SelectionChanged += (_, _) => UpdateSourceKey();
        _controller.StatusMessage += (_, message) =>
        {
            StatusText.Text = message;
            _tray?.SetStatus(message);
        };
        _controller.PauseChanged += (_, paused) =>
        {
            PauseButton.Content = paused ? "Продолжить" : "Пауза";
            _tray?.SetPaused(paused);
        };
        _controller.ProfileChanged += (_, profile) => ProfilesList.SelectedItem = profile;
        ProfileMonitorBox.ItemTemplate = new Avalonia.Controls.Templates.FuncDataTemplate<MonitorInfo>(
            (monitor, _) => new TextBlock
            {
                Text = $"{monitor.Name} — {monitor.Width}×{monitor.Height}"
            },
            supportsRecycling: true);
        if (Application.Current is { } application && WallflowTrayService.IsSupported())
        {
            _tray = new WallflowTrayService(
                application,
                ShowWindow,
                TogglePauseFromHotkey,
                NextFromHotkey,
                PreviousFromHotkey,
                OpenWallpaperFolder,
                QuitApplication);
            _tray.SetPaused(_controller.Paused);
        }

        _hotkeys = new GlobalHotkeyManager(
            this,
            TogglePauseFromHotkey,
            NextFromHotkey,
            PreviousFromHotkey,
            ShowWindow,
            message => StatusText.Text = message);
        Opened += OnWindowOpened;
        Closing += OnWindowClosing;
        PropertyChanged += OnWindowPropertyChanged;
        KeyDown += OnMainWindowKeyDown;
        try
        {
            var count = _library.Scan();
            RefreshWallpapers($"Библиотека загружена. Найдено файлов: {count}.");
        }
        catch (IOException ex)
        {
            RefreshWallpapers($"Ошибка сканирования: {ex.Message}");
        }
        catch (UnauthorizedAccessException ex)
        {
            RefreshWallpapers($"Нет доступа к папке: {ex.Message}");
        }
        catch (Exception ex) when (ex is InvalidOperationException or NotSupportedException or Win32Exception)
        {
            RefreshWallpapers($"Не удалось восстановить профиль при запуске: {ex.Message}");
        }
        LoadSettings();
        Closed += (_, _) =>
        {
            _hotkeys.Dispose();
            _tray?.Dispose();
            _controller.Dispose();
            _playback.Dispose();
        };
    }

    private void OnWindowOpened(object? sender, EventArgs e)
    {
        Program.InstanceGuard?.StartQuitListener(
            () => Avalonia.Threading.Dispatcher.UIThread.Post(QuitApplication));

        if (App.StartupCommand.ListMonitors)
        {
            PrintMonitors();
            Hide();
            Avalonia.Threading.Dispatcher.UIThread.Post(QuitApplication);
            return;
        }

        if (App.StartupCommand.SetPath is { } wallpaperPath)
        {
            ApplyCommandLineWallpaper(wallpaperPath);
            return;
        }

        try
        {
            _controller.Start();
            RefreshProfileMonitorControls();
        }
        catch (Exception exception) when (
            exception is IOException or InvalidOperationException or NotSupportedException or Win32Exception)
        {
            StatusText.Text = $"Не удалось запустить воспроизведение: {exception.Message}";
        }

        if (App.StartupCommand.ProfileName is { } profileName)
        {
            var profile = _controller.Profiles.FirstOrDefault(item =>
                string.Equals(item.Name, profileName, StringComparison.OrdinalIgnoreCase));
            if (profile is null || !_controller.ActivateProfile(profile.Id))
            {
                Console.Error.WriteLine($"Profile not found: {profileName}");
                Environment.ExitCode = 2;
                Avalonia.Threading.Dispatcher.UIThread.Post(QuitApplication);
                return;
            }
        }

        if (!App.StartMinimized)
            return;
        if (_tray is null)
        {
            StatusText.Text = "Значок системного трея недоступен; окно оставлено открытым.";
            return;
        }
        Hide();
    }

    private void PrintMonitors()
    {
        var session = DesktopEnvironment.Detect();
        var suffix = session.IsWayland ? ", Wayland" : "";
        Console.WriteLine($"System: {session.Label}  ({session.Desktop}); session: {session.SessionType}{suffix}");
        Console.WriteLine($"  per-monitor wallpapers: {(_playback.Capabilities.PerMonitor ? "yes" : "no")}");
        Console.WriteLine($"  accent colour: {(_playback.Capabilities.AccentColor ? "yes" : "no")}");
        Console.WriteLine($"  backend: {_playback.BackendName}");
        Console.WriteLine($"  video runtime: {(VideoRuntime.IsAvailable ? "available (LibVLC)" : "unavailable")}");
        Console.WriteLine("Monitors:");
        var screens = Screens.All;
        for (var index = 0; index < screens.Count; index++)
        {
            var screen = screens[index];
            var bounds = screen.Bounds;
            var primary = screen.IsPrimary ? " primary" : "";
            Console.WriteLine(
                $"  {index + 1}. {screen.DisplayName ?? $"monitor-{index + 1}"} " +
                $"{bounds.Width}x{bounds.Height} @({bounds.X},{bounds.Y}){primary}");
        }
        if (screens.Count == 0)
            Console.WriteLine("  (none detected)");
    }

    private void ApplyCommandLineWallpaper(string path)
    {
        var fullPath = Path.GetFullPath(path);
        if (!File.Exists(fullPath) && !Directory.Exists(fullPath))
        {
            Console.Error.WriteLine($"Wallpaper path does not exist: {fullPath}");
            Environment.ExitCode = 2;
            Avalonia.Threading.Dispatcher.UIThread.Post(QuitApplication);
            return;
        }

        var wallpaper = Wallpaper.FromPath(fullPath);
        if (File.Exists(fullPath) && !WallpaperLibrary.IsUsable(fullPath))
        {
            Console.Error.WriteLine($"Wallpaper file is empty: {fullPath}");
            Environment.ExitCode = 2;
            Avalonia.Threading.Dispatcher.UIThread.Post(QuitApplication);
            return;
        }
        if (wallpaper.WallpaperType == WallpaperKind.Web && Directory.Exists(fullPath) &&
            !File.Exists(Path.Combine(fullPath, "index.html")))
        {
            Console.Error.WriteLine($"HTML wallpaper folder has no index.html: {fullPath}");
            Environment.ExitCode = 2;
            Avalonia.Threading.Dispatcher.UIThread.Post(QuitApplication);
            return;
        }

        try
        {
            _playback.SetCurrent(wallpaper);
            Console.WriteLine($"Applied wallpaper: {wallpaper.Name}");
            if (wallpaper.WallpaperType == WallpaperKind.Image &&
                !Path.GetExtension(wallpaper.Path).Equals(".gif", StringComparison.OrdinalIgnoreCase))
            {
                var timer = new Avalonia.Threading.DispatcherTimer
                {
                    Interval = TimeSpan.FromMilliseconds(1500)
                };
                timer.Tick += (_, _) =>
                {
                    timer.Stop();
                    QuitApplication();
                };
                timer.Start();
            }
        }
        catch (Exception exception) when (
            exception is IOException or InvalidOperationException or NotSupportedException or Win32Exception)
        {
            Console.Error.WriteLine($"Could not apply wallpaper: {exception.Message}");
            Environment.ExitCode = 1;
            Avalonia.Threading.Dispatcher.UIThread.Post(QuitApplication);
        }
    }

    private void OnWindowClosing(object? sender, WindowClosingEventArgs e)
    {
        if (_allowExit || _tray is null || !_config.Get("close_to_tray", true))
            return;
        e.Cancel = true;
        Hide();
    }

    private void OnWindowPropertyChanged(object? sender, AvaloniaPropertyChangedEventArgs e)
    {
        if (e.Property == Window.WindowStateProperty &&
            WindowState == WindowState.Minimized &&
            _tray is not null &&
            _config.Get("minimize_to_tray", true))
            Hide();
    }

    private void OnMainWindowKeyDown(object? sender, KeyEventArgs e)
    {
        if (e.KeyModifiers != KeyModifiers.Control)
            return;
        switch (e.Key)
        {
            case Key.Space:
                TogglePauseFromHotkey();
                e.Handled = true;
                break;
            case Key.Left:
                PreviousFromHotkey();
                e.Handled = true;
                break;
            case Key.Right:
                NextFromHotkey();
                e.Handled = true;
                break;
            case Key.Q:
                QuitApplication();
                e.Handled = true;
                break;
            case Key.D1:
                ShowPage("library");
                e.Handled = true;
                break;
            case Key.D2:
                ShowPage("sources");
                e.Handled = true;
                break;
            case Key.D3:
                ShowPage("playlists");
                e.Handled = true;
                break;
            case Key.D4:
                ShowPage("profiles");
                e.Handled = true;
                break;
            case Key.D5:
                ShowPage("schedule");
                e.Handled = true;
                break;
            case Key.D6:
                ShowPage("settings");
                e.Handled = true;
                break;
        }
    }

    private void TogglePauseFromHotkey()
    {
        try
        {
            _controller.TogglePause();
        }
        catch (IOException ex)
        {
            StatusText.Text = $"Ошибка переключения паузы: {ex.Message}";
        }
    }

    private void NextFromHotkey() => RunNavigationAction(_controller.Next, "Не удалось переключить обои");
    private void PreviousFromHotkey() => RunNavigationAction(_controller.Previous, "Не удалось переключить обои");

    private void RunNavigationAction(Action action, string errorPrefix)
    {
        try
        {
            action();
        }
        catch (Exception ex) when (
            ex is IOException or InvalidOperationException or NotSupportedException or Win32Exception)
        {
            StatusText.Text = $"{errorPrefix}: {ex.Message}";
        }
    }

    private void ShowWindow()
    {
        if (WindowState == WindowState.Minimized)
            WindowState = WindowState.Normal;
        Show();
        Activate();
    }

    private void OpenWallpaperFolder()
    {
        try
        {
            Directory.CreateDirectory(WallflowPaths.WallpaperDirectory);
            var executable = OperatingSystem.IsWindows()
                ? "explorer.exe"
                : OperatingSystem.IsMacOS() ? "open" : "xdg-open";
            var startInfo = new ProcessStartInfo(executable) { UseShellExecute = false };
            startInfo.ArgumentList.Add(WallflowPaths.WallpaperDirectory);
            Process.Start(startInfo);
        }
        catch (Exception ex) when (ex is IOException or Win32Exception or InvalidOperationException)
        {
            StatusText.Text = $"Не удалось открыть папку обоев: {ex.Message}";
        }
    }

    private void QuitApplication()
    {
        _allowExit = true;
        if (Application.Current?.ApplicationLifetime is IClassicDesktopStyleApplicationLifetime desktop)
            desktop.Shutdown(Environment.ExitCode);
    }

    private void NavigateClick(object? sender, RoutedEventArgs e)
    {
        var page = (sender as Button)?.Tag as string ?? "library";
        ShowPage(page);
    }

    private void ShowPage(string page)
    {
        LibraryPage.IsVisible = page == "library";
        SourcesPage.IsVisible = page == "sources";
        PlaylistsPage.IsVisible = page == "playlists";
        ProfilesPage.IsVisible = page == "profiles";
        SchedulePage.IsVisible = page == "schedule";
        SettingsPage.IsVisible = page == "settings";
        PageTitle.Text = page switch
        {
            "sources" => "Источники обоев",
            "playlists" => "Плейлисты",
            "profiles" => "Профили",
            "schedule" => "Расписание",
            "settings" => "Настройки",
            _ => "Библиотека обоев"
        };
    }

    private async void AddFilesClick(object? sender, RoutedEventArgs e)
    {
        try
        {
            var files = await StorageProvider.OpenFilePickerAsync(new FilePickerOpenOptions
            {
                Title = "Добавить обои",
                AllowMultiple = true,
                FileTypeFilter =
                [
                    new FilePickerFileType("Файлы обоев")
                    {
                        Patterns = WallpaperLibrary.SupportedPatterns
                    }
                ]
            });

            var added = _library.AddFiles(files.Select(file => file.TryGetLocalPath()).OfType<string>());
            RefreshWallpapers($"Добавлено файлов: {added}");
        }
        catch (IOException ex)
        {
            StatusText.Text = $"Не удалось добавить файлы: {ex.Message}";
        }
        catch (UnauthorizedAccessException ex)
        {
            StatusText.Text = $"Нет доступа к выбранному файлу: {ex.Message}";
        }
    }

    private void SetProfileWallpaperClick(object? sender, RoutedEventArgs e)
    {
        if (ProfilesList.SelectedItem is not Profile profile)
            return;
        if (WallpaperList.SelectedItem is not Wallpaper wallpaper)
        {
            StatusText.Text = "Выберите обои в библиотеке, затем назначьте их профилю.";
            return;
        }
        try
        {
            _controller.SetProfileWallpaper(profile.Id, wallpaper.Id);
            ProfilesList.ItemsSource = null;
            ProfilesList.ItemsSource = _controller.Profiles;
            ProfilesList.SelectedItem = _controller.FindProfile(profile.Id);
            StatusText.Text = $"Обои назначены профилю «{profile.Name}».";
        }
        catch (Exception ex) when (ex is IOException or InvalidOperationException or NotSupportedException or Win32Exception)
        {
            StatusText.Text = $"Не удалось назначить обои: {ex.Message}";
        }
    }

    private void RenameProfileClick(object? sender, RoutedEventArgs e)
    {
        if (ProfilesList.SelectedItem is not Profile profile)
        {
            StatusText.Text = "Выберите профиль.";
            return;
        }
        try
        {
            _controller.RenameProfile(profile.Id, ProfileEditNameBox.Text ?? "");
            RefreshProfiles(profile);
            StatusText.Text = $"Профиль переименован: «{profile.Name}».";
        }
        catch (Exception ex) when (ex is IOException or ArgumentException or InvalidOperationException)
        {
            StatusText.Text = $"Не удалось переименовать профиль: {ex.Message}";
        }
    }

    private void SetProfilePlaylistClick(object? sender, RoutedEventArgs e)
    {
        if (ProfilesList.SelectedItem is not Profile profile)
        {
            StatusText.Text = "Выберите профиль.";
            return;
        }
        try
        {
            var playlist = ProfilePlaylistBox.SelectedItem as Playlist;
            _controller.SetProfilePlaylist(profile.Id, playlist?.Id);
            RefreshProfiles(profile);
            StatusText.Text = playlist is null
                ? $"Плейлист профиля «{profile.Name}» очищен."
                : $"Плейлист «{playlist.Name}» назначен профилю «{profile.Name}».";
        }
        catch (Exception ex) when (
            ex is IOException or ArgumentException or InvalidOperationException or
                NotSupportedException or Win32Exception)
        {
            StatusText.Text = $"Не удалось изменить плейлист профиля: {ex.Message}";
        }
    }

    private void SetStartupProfileClick(object? sender, RoutedEventArgs e)
    {
        if (StartupProfileBox.SelectedItem is not Profile profile)
        {
            StatusText.Text = "Выберите профиль запуска.";
            return;
        }
        try
        {
            _config.Set("startup_profile_id", profile.Id);
            StatusText.Text = $"При запуске будет выбран профиль «{profile.Name}».";
        }
        catch (IOException ex)
        {
            StatusText.Text = $"Не удалось сохранить профиль запуска: {ex.Message}";
        }
    }

    private void ClearStartupProfileClick(object? sender, RoutedEventArgs e)
    {
        try
        {
            _config.Set("startup_profile_id", "");
            StartupProfileBox.SelectedItem = null;
            StatusText.Text = "Профиль запуска очищен.";
        }
        catch (IOException ex)
        {
            StatusText.Text = $"Не удалось очистить профиль запуска: {ex.Message}";
        }
    }

    private void AssignMonitorWallpaperClick(object? sender, RoutedEventArgs e)
    {
        if (ProfileMonitorBox.SelectedItem is not MonitorInfo monitor ||
            WallpaperList.SelectedItem is not Wallpaper wallpaper)
        {
            StatusText.Text = "Выберите монитор и обои в библиотеке.";
            return;
        }
        try
        {
            _controller.SetMonitorWallpaper(
                monitor.Id,
                wallpaper.Id,
                GetComboTag(ProfileMonitorFitBox, "fill"),
                GetComboTag(ProfileMonitorAlignmentBox, "center"));
            RefreshProfileMonitorControls();
            StatusText.Text = $"«{wallpaper.Name}» назначены монитору {monitor.Name}.";
        }
        catch (Exception exception) when (
            exception is IOException or InvalidOperationException or NotSupportedException or Win32Exception)
        {
            StatusText.Text = $"Не удалось назначить обои монитору: {exception.Message}";
        }
    }

    private void ClearMonitorWallpaperClick(object? sender, RoutedEventArgs e)
    {
        if (ProfileMonitorBox.SelectedItem is not MonitorInfo monitor)
            return;
        try
        {
            _controller.SetMonitorWallpaper(
                monitor.Id,
                null,
                GetComboTag(ProfileMonitorFitBox, "fill"),
                GetComboTag(ProfileMonitorAlignmentBox, "center"));
            RefreshProfileMonitorControls();
            StatusText.Text = $"Монитор {monitor.Name} будет использовать основные обои.";
        }
        catch (Exception exception) when (
            exception is IOException or InvalidOperationException or NotSupportedException or Win32Exception)
        {
            StatusText.Text = $"Не удалось сбросить назначение монитора: {exception.Message}";
        }
    }

    private void RefreshProfileMonitorControls()
    {
        var selectedId = (ProfileMonitorBox.SelectedItem as MonitorInfo)?.Id;
        ProfileMonitorBox.ItemsSource = _controller.Monitors;
        ProfileMonitorBox.SelectedItem = _controller.Monitors.FirstOrDefault(monitor => monitor.Id == selectedId)
                                        ?? _controller.Monitors.FirstOrDefault(monitor => monitor.Primary)
                                        ?? _controller.Monitors.FirstOrDefault();
        UpdateProfileMonitorAssignment();
    }

    private void UpdateProfileMonitorAssignment()
    {
        if (ProfilesList.SelectedItem is not Profile profile ||
            ProfileMonitorBox.SelectedItem is not MonitorInfo monitor)
        {
            ProfileMonitorAssignmentText.Text = "Нет доступных мониторов.";
            return;
        }

        var assignment = profile.Monitors.FirstOrDefault(item => item.MonitorId == monitor.Id);
        _loadingProfileMonitorSettings = true;
        SelectComboTag(ProfileMonitorFitBox, assignment?.Fit ?? "fill");
        SelectComboTag(ProfileMonitorAlignmentBox, assignment?.Alignment ?? "center");
        _loadingProfileMonitorSettings = false;
        var wallpaper = assignment?.WallpaperId is { } id ? _library.Get(id) : null;
        ProfileMonitorAssignmentText.Text = wallpaper is null
            ? "Основные обои профиля"
            : $"Назначено: {wallpaper.Name}";
    }

    private void ProfileMonitorFitSelectionChanged(object? sender, SelectionChangedEventArgs e)
    {
        if (_loadingProfileMonitorSettings ||
            ProfilesList.SelectedItem is not Profile profile ||
            ProfileMonitorBox.SelectedItem is not MonitorInfo monitor)
            return;
        var assignment = profile.Monitors.FirstOrDefault(item => item.MonitorId == monitor.Id);
        try
        {
            _controller.SetMonitorWallpaper(
                monitor.Id,
                assignment?.WallpaperId,
                GetComboTag(ProfileMonitorFitBox, "fill"),
                GetComboTag(ProfileMonitorAlignmentBox, "center"));
            UpdateProfileMonitorAssignment();
        }
        catch (Exception exception) when (
            exception is IOException or InvalidOperationException or NotSupportedException or Win32Exception)
        {
            StatusText.Text = $"Не удалось сохранить параметры монитора: {exception.Message}";
        }
    }

    private static string GetComboTag(ComboBox comboBox, string fallback) =>
        (comboBox.SelectedItem as ComboBoxItem)?.Tag as string ?? fallback;

    private static void SelectComboTag(ComboBox comboBox, string value)
    {
        comboBox.SelectedItem = comboBox.Items
            .OfType<ComboBoxItem>()
            .FirstOrDefault(item => string.Equals(item.Tag as string, value, StringComparison.Ordinal));
    }

    private void ProfileMonitorSelectionChanged(object? sender, SelectionChangedEventArgs e) =>
        UpdateProfileMonitorAssignment();

    private async void AddFolderClick(object? sender, RoutedEventArgs e)
    {
        try
        {
            var folders = await StorageProvider.OpenFolderPickerAsync(new FolderPickerOpenOptions
            {
                Title = "Выбрать папку с обоями",
                AllowMultiple = true
            });

            var paths = folders.Select(folder => folder.TryGetLocalPath()).OfType<string>();
            _config.AddLibraryDirectories(paths);
            RefreshWallpapers("Папки добавлены; выполняется сканирование.");
            ScanClick(sender, e);
        }
        catch (IOException ex)
        {
            StatusText.Text = $"Не удалось сохранить настройки папок: {ex.Message}";
        }
        catch (UnauthorizedAccessException ex)
        {
            StatusText.Text = $"Нет доступа к выбранной папке: {ex.Message}";
        }
    }

    private void RemoveRuleClick(object? sender, RoutedEventArgs e)
    {
        if (RulesList.SelectedItem is not ScheduleRule rule)
            return;
        try
        {
            if (_controller.RemoveRule(rule.Id))
            {
                RefreshRules();
                StatusText.Text = $"Правило «{rule.Name}» удалено.";
            }
        }
        catch (IOException ex)
        {
            StatusText.Text = $"Не удалось удалить правило: {ex.Message}";
        }
    }

    private void ScanClick(object? sender, RoutedEventArgs e)
    {
        try
        {
            var count = _library.Scan();
            RefreshWallpapers($"Сканирование завершено. В библиотеке: {count}.");
        }
        catch (IOException ex)
        {
            StatusText.Text = $"Ошибка сканирования: {ex.Message}";
        }
        catch (UnauthorizedAccessException ex)
        {
            StatusText.Text = $"Нет доступа к папке: {ex.Message}";
        }
    }

    private void SearchTextChanged(object? sender, TextChangedEventArgs e) =>
        RefreshWallpapers(StatusText.Text);

    private void WallpaperSelectionChanged(object? sender, SelectionChangedEventArgs e)
    {
        var selected = WallpaperList.SelectedItem as Wallpaper;
        FavoriteButton.IsEnabled = selected is not null;
        PreviewSceneButton.IsEnabled = selected is not null;
        ApplyButton.IsEnabled = selected is not null;
        PropertiesButton.IsEnabled = selected is not null;
        OpenFolderButton.IsEnabled = selected is not null;
        CopyPathButton.IsEnabled = selected is not null;
        DeleteButton.IsEnabled = selected is not null;
        FavoriteButton.Content = selected?.Favourite == true ? "Убрать из избранного" : "В избранное";
        WallpaperNameBox.Text = selected?.Name ?? "";
        WallpaperAuthorBox.Text = selected?.Author ?? "";
        WallpaperTagsBox.Text = selected is null ? "" : string.Join(", ", selected.Tags);
        WallpaperDescriptionBox.Text = selected?.Description ?? "";
    }

    private void WallpaperFilterChanged(object? sender, RoutedEventArgs e) =>
        RefreshWallpapers(StatusText.Text);

    private void WallpaperFilterChanged(object? sender, SelectionChangedEventArgs e) =>
        RefreshWallpapers(StatusText.Text);

    private void SaveWallpaperMetadataClick(object? sender, RoutedEventArgs e)
    {
        if (WallpaperList.SelectedItem is not Wallpaper selected)
        {
            StatusText.Text = "Выберите обои для редактирования.";
            return;
        }
        var name = WallpaperNameBox.Text?.Trim() ?? "";
        if (name.Length == 0)
        {
            StatusText.Text = "Название обоев не может быть пустым.";
            return;
        }
        try
        {
            var tags = (WallpaperTagsBox.Text ?? "")
                .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
                .Distinct(StringComparer.Ordinal)
                .ToArray();
            if (!_library.Update(
                    selected.Id,
                    name,
                    WallpaperAuthorBox.Text?.Trim() ?? "",
                    WallpaperDescriptionBox.Text?.Trim() ?? "",
                    tags))
            {
                StatusText.Text = "Обои больше не находятся в библиотеке.";
                return;
            }
            RefreshWallpapers($"Описание «{name}» сохранено.");
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or InvalidOperationException)
        {
            StatusText.Text = $"Не удалось сохранить описание обоев: {ex.Message}";
        }
    }

    private void FavoriteClick(object? sender, RoutedEventArgs e)
    {
        if (WallpaperList.SelectedItem is not Wallpaper selected)
            return;

        _library.SetFavourite(selected.Id, !selected.Favourite);
        RefreshWallpapers(StatusText.Text);
    }

    private void ApplyClick(object? sender, RoutedEventArgs e)
    {
        if (WallpaperList.SelectedItem is not Wallpaper selected)
            return;

        try
        {
            _controller.SetWallpaper(selected.Id);
        }
        catch (Exception ex) when (ex is IOException or InvalidOperationException or Win32Exception or NotSupportedException)
        {
            StatusText.Text = $"Не удалось применить обои: {ex.Message}";
        }
    }

    private async void PropertiesClick(object? sender, RoutedEventArgs e)
    {
        if (WallpaperList.SelectedItem is not Wallpaper selected)
            return;
        try
        {
            await WallpaperDialogs.ShowPropertiesAsync(this, selected, _controller.Monitors);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or InvalidOperationException)
        {
            StatusText.Text = $"Не удалось показать свойства: {ex.Message}";
        }
    }

    private void OpenWallpaperFolderClick(object? sender, RoutedEventArgs e)
    {
        if (WallpaperList.SelectedItem is not Wallpaper selected)
            return;
        OpenInFileManager(selected.Path, select: File.Exists(selected.Path));
    }

    private async void CopyPathClick(object? sender, RoutedEventArgs e)
    {
        if (WallpaperList.SelectedItem is not Wallpaper selected)
            return;
        if (Clipboard is { } clipboard)
        {
            await clipboard.SetTextAsync(selected.Path);
            StatusText.Text = "Путь скопирован в буфер обмена.";
        }
    }

    private async void DeleteWallpaperClick(object? sender, RoutedEventArgs e)
    {
        if (WallpaperList.SelectedItem is not Wallpaper selected)
            return;
        var choice = WallpaperDeleteChoice.Cancel;
        try
        {
            choice = await WallpaperDialogs.ConfirmDeleteAsync(this, selected);
        }
        catch (InvalidOperationException ex)
        {
            StatusText.Text = $"Не удалось открыть подтверждение: {ex.Message}";
            return;
        }

        if (choice == WallpaperDeleteChoice.Cancel)
            return;
        try
        {
            if (!_library.Remove(selected.Id, deleteFile: choice == WallpaperDeleteChoice.DeleteFile))
                return;
            RefreshWallpapers(choice == WallpaperDeleteChoice.DeleteFile
                ? $"Обои «{selected.Name}» удалены с диска."
                : $"Обои «{selected.Name}» убраны из библиотеки.");
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or InvalidOperationException)
        {
            StatusText.Text = $"Не удалось удалить обои: {ex.Message}";
        }
    }

    /// <summary>Opens the file manager at a wallpaper, selecting the file on Windows.</summary>
    private void OpenInFileManager(string path, bool select)
    {
        try
        {
            var directory = Directory.Exists(path) ? path : Path.GetDirectoryName(path) ?? path;
            Directory.CreateDirectory(directory);
            var executable = OperatingSystem.IsWindows()
                ? "explorer.exe"
                : OperatingSystem.IsMacOS() ? "open" : "xdg-open";
            var startInfo = new ProcessStartInfo(executable) { UseShellExecute = false };
            if (OperatingSystem.IsWindows() && select)
                startInfo.ArgumentList.Add($"/select,{path}");
            else
                startInfo.ArgumentList.Add(directory);
            Process.Start(startInfo);
        }
        catch (Exception ex) when (ex is IOException or Win32Exception or InvalidOperationException)
        {
            StatusText.Text = $"Не удалось открыть папку: {ex.Message}";
        }
    }

    private void OpenDataFolderClick(object? sender, RoutedEventArgs e)
    {
        try
        {
            WallflowPaths.EnsureDirectories();
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            StatusText.Text = $"Не удалось создать папку данных: {ex.Message}";
            return;
        }
        OpenInFileManager(WallflowPaths.DataDirectory, select: false);
    }

    private void OpenLogFolderClick(object? sender, RoutedEventArgs e)
    {
        try
        {
            Directory.CreateDirectory(WallflowPaths.LogDirectory);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            StatusText.Text = $"Не удалось создать папку журналов: {ex.Message}";
            return;
        }
        OpenInFileManager(WallflowPaths.LogDirectory, select: false);
    }

    private async void ResetSettingsClick(object? sender, RoutedEventArgs e)
    {
        var confirmed = await ConfirmAsync(
            "Сбросить настройки",
            "Настройки вернутся к значениям по умолчанию. Библиотека, профили и расписание сохранятся.");
        if (!confirmed)
            return;
        try
        {
            _config.ResetDefaults();
            LoadSettings();
            StatusText.Text = "Настройки сброшены.";
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or InvalidOperationException)
        {
            StatusText.Text = $"Не удалось сбросить настройки: {ex.Message}";
        }
    }

    private void RemoveLibraryDirectoryClick(object? sender, RoutedEventArgs e)
    {
        if (LibraryDirectoriesList.SelectedItem is not string directory)
        {
            StatusText.Text = "Выберите папку, которую нужно убрать.";
            return;
        }
        try
        {
            _config.RemoveLibraryDirectory(directory);
            RefreshLibraryDirectories();
            RefreshWallpapers($"Папка убрана: {directory}");
        }
        catch (IOException ex)
        {
            StatusText.Text = $"Не удалось сохранить список папок: {ex.Message}";
        }
    }

    private async void ChooseDownloadFolderClick(object? sender, RoutedEventArgs e)
    {
        var folders = await StorageProvider.OpenFolderPickerAsync(new FolderPickerOpenOptions
        {
            Title = "Папка загрузок",
            AllowMultiple = false
        });
        var path = folders.Select(folder => folder.TryGetLocalPath()).FirstOrDefault(item => item is not null);
        if (path is null)
            return;
        try
        {
            _config.Set("download_folder", path);
            UpdateDownloadFolder();
            StatusText.Text = $"Загрузки будут сохраняться в {path}.";
        }
        catch (IOException ex)
        {
            StatusText.Text = $"Не удалось сохранить папку загрузок: {ex.Message}";
        }
    }

    private void ResetDownloadFolderClick(object? sender, RoutedEventArgs e)
    {
        try
        {
            _config.Set("download_folder", "");
            UpdateDownloadFolder();
            StatusText.Text = "Загрузки вернутся в папку обоев.";
        }
        catch (IOException ex)
        {
            StatusText.Text = $"Не удалось сбросить папку загрузок: {ex.Message}";
        }
    }

    private void AccentColorSelectionChanged(object? sender, SelectionChangedEventArgs e)
    {
        if (_loadingSettings)
            return;
        try
        {
            var accent = GetComboTag(AccentColorBox, "auto");
            _config.Set("accent_color", accent);
            if (accent.StartsWith('#') && _controller.ActiveProfile is { } profile && profile.AccentColor.Length == 0)
                _playback.ApplyAccent(accent);
            else if (accent is "auto" or "none")
                _controller.ReapplyAccent();
            StatusText.Text = "Акцентный цвет сохранён.";
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            StatusText.Text = $"Не удалось сохранить акцент: {ex.Message}";
        }
        catch (NotSupportedException ex)
        {
            StatusText.Text = $"Акцентный цвет не поддерживается: {ex.Message}";
        }
    }

    private void ThumbSizeSelectionChanged(object? sender, SelectionChangedEventArgs e)
    {
        if (_loadingSettings)
            return;
        if (!int.TryParse(GetComboTag(ThumbSizeBox, "220"), out var size))
            return;
        try
        {
            _config.Set("thumb_size", size);
            _thumbnails.Clear();
            _thumbnails = new ThumbnailCache(size);
            RefreshWallpapers("Размер миниатюр сохранён.");
        }
        catch (IOException ex)
        {
            StatusText.Text = $"Не удалось сохранить размер миниатюр: {ex.Message}";
        }
    }

    private Task<bool> ConfirmAsync(string title, string message) =>
        WallpaperDialogs.ConfirmAsync(this, title, message, "Сбросить");

    private void PreviewSceneClick(object? sender, RoutedEventArgs e)
    {
        if (WallpaperList.SelectedItem is not Wallpaper selected)
        {
            StatusText.Text = "Выберите обои из библиотеки.";
            return;
        }

        try
        {
            var control = new LiveWallpaperControl(
                selected,
                loopVideos: _config.Get("loop_videos", true),
                videoAudio: _config.Get("video_audio", false),
                maxFps: _config.Get("max_fps", 30));
            control.PlaybackError += (_, message) => StatusText.Text = message;
            control.SourceResolutionResolved += (_, size) =>
            {
                var widest = _controller.Monitors.OrderByDescending(monitor => monitor.Width).FirstOrDefault();
                if (widest is not null && size.Width < widest.Width)
                    StatusText.Text = WallpaperSurfaceWindow.UpscaleWarning(selected.Name, size, widest.Width, widest.Height);
            };
            var preview = new Window
            {
                Title = $"Предпросмотр — {selected.Name}",
                Width = 960,
                Height = 540,
                MinWidth = 480,
                MinHeight = 270,
                Content = control
            };
            preview.Opened += async (_, _) =>
            {
                try
                {
                    await control.StartAsync();
                }
                catch (Exception ex) when (
                    ex is IOException or UnauthorizedAccessException or InvalidDataException or
                        InvalidOperationException or NotSupportedException or System.Text.Json.JsonException)
                {
                    StatusText.Text = $"Не удалось воспроизвести обои: {ex.Message}";
                }
            };
            preview.Closed += (_, _) => control.Dispose();
            preview.Show(this);
            StatusText.Text = $"Открыт предпросмотр «{selected.Name}».";
        }
        catch (Exception ex) when (
            ex is IOException or UnauthorizedAccessException or InvalidDataException or
                InvalidOperationException or NotSupportedException or System.Text.Json.JsonException)
        {
            StatusText.Text = $"Не удалось открыть предпросмотр: {ex.Message}";
        }
    }

    private void AddProfileClick(object? sender, RoutedEventArgs e)
    {
        try
        {
            var profile = _controller.AddProfile(ProfileNameBox.Text ?? "", (WallpaperList.SelectedItem as Wallpaper)?.Id);
            RefreshProfiles(profile);
            ProfileNameBox.Text = "";
            StatusText.Text = $"Профиль «{profile.Name}» создан.";
        }
        catch (ArgumentException ex)
        {
            StatusText.Text = ex.Message;
        }
        catch (IOException ex)
        {
            StatusText.Text = $"Не удалось сохранить профиль: {ex.Message}";
        }
    }

    private void DuplicateProfileClick(object? sender, RoutedEventArgs e)
    {
        if (ProfilesList.SelectedItem is not Profile profile)
        {
            StatusText.Text = "Выберите профиль для копирования.";
            return;
        }

        try
        {
            var copy = _controller.DuplicateProfile(profile.Id);
            RefreshProfiles(copy);
            StatusText.Text = $"Создана копия профиля «{copy.Name}».";
        }
        catch (Exception ex) when (ex is IOException or ArgumentException or InvalidOperationException)
        {
            StatusText.Text = $"Не удалось скопировать профиль: {ex.Message}";
        }
    }

    private void ActivateProfileClick(object? sender, RoutedEventArgs e)
    {
        if (ProfilesList.SelectedItem is not Profile profile)
            return;
        try
        {
            _controller.ActivateProfile(profile.Id);
        }
        catch (Exception ex) when (ex is IOException or InvalidOperationException or NotSupportedException or Win32Exception)
        {
            StatusText.Text = $"Не удалось активировать профиль: {ex.Message}";
        }
    }

    private void RemoveProfileClick(object? sender, RoutedEventArgs e)
    {
        if (ProfilesList.SelectedItem is not Profile profile)
            return;
        try
        {
            if (!_controller.RemoveProfile(profile.Id))
            {
                StatusText.Text = "Нельзя удалить этот профиль: это единственный или защищённый профиль.";
                return;
            }
            RefreshProfiles();
            StatusText.Text = $"Профиль «{profile.Name}» удалён.";
        }
        catch (IOException ex)
        {
            StatusText.Text = $"Не удалось удалить профиль: {ex.Message}";
        }
    }

    private void AddPlaylistClick(object? sender, RoutedEventArgs e)
    {
        try
        {
            var selected = WallpaperList.SelectedItem as Wallpaper;
            var playlist = _controller.AddPlaylist(
                PlaylistNameBox.Text ?? "",
                selected is null ? [] : [selected.Id]);
            RefreshPlaylists(playlist);
            PlaylistNameBox.Text = "";
            StatusText.Text = $"Плейлист «{playlist.Name}» создан.";
        }
        catch (ArgumentException ex)
        {
            StatusText.Text = ex.Message;
        }
        catch (IOException ex)
        {
            StatusText.Text = $"Не удалось сохранить плейлист: {ex.Message}";
        }
    }

    private void ActivatePlaylistClick(object? sender, RoutedEventArgs e)
    {
        if (PlaylistsList.SelectedItem is not Playlist playlist)
            return;
        if (_controller.ActiveProfile is null)
        {
            StatusText.Text = "Сначала создайте или выберите профиль.";
            return;
        }
        try
        {
            _controller.SetPlaylist(playlist.Id);
        }
        catch (Exception ex) when (ex is IOException or InvalidOperationException or NotSupportedException or Win32Exception)
        {
            StatusText.Text = $"Не удалось активировать плейлист: {ex.Message}";
        }
    }

    private void AddRuleClick(object? sender, RoutedEventArgs e)
    {
        var rule = new ScheduleRule();
        if (!TryReadRuleEditor(rule))
            return;
        try
        {
            _controller.AddRule(rule);
            RefreshRules(rule);
            StatusText.Text = $"Правило «{rule.Name}» создано.";
        }
        catch (IOException ex)
        {
            StatusText.Text = $"Не удалось сохранить расписание: {ex.Message}";
        }
    }

    private void SaveRuleClick(object? sender, RoutedEventArgs e)
    {
        if (RulesList.SelectedItem is not ScheduleRule rule)
        {
            StatusText.Text = "Выберите правило для изменения.";
            return;
        }
        if (!TryReadRuleEditor(rule))
            return;
        try
        {
            _controller.UpdateRule(rule);
            RefreshRules(rule);
            StatusText.Text = $"Правило «{rule.Name}» сохранено.";
        }
        catch (Exception ex) when (ex is IOException or ArgumentException or InvalidOperationException)
        {
            StatusText.Text = $"Не удалось сохранить правило: {ex.Message}";
        }
    }

    private void ToggleRuleClick(object? sender, RoutedEventArgs e)
    {
        if (RulesList.SelectedItem is not ScheduleRule rule)
        {
            StatusText.Text = "Выберите правило.";
            return;
        }
        try
        {
            rule.Enabled = !rule.Enabled;
            _controller.UpdateRule(rule);
            RefreshRules(rule);
            StatusText.Text = $"Правило «{rule.Name}» {(rule.Enabled ? "включено" : "выключено")}.";
        }
        catch (Exception ex) when (ex is IOException or ArgumentException or InvalidOperationException)
        {
            StatusText.Text = $"Не удалось изменить правило: {ex.Message}";
        }
    }

    private void RuleSelectionChanged(object? sender, SelectionChangedEventArgs e)
    {
        if (RulesList.SelectedItem is not ScheduleRule rule)
            return;
        RuleNameBox.Text = rule.Name;
        RuleStartBox.Text = TimeOnly.FromTimeSpan(TimeSpan.FromMinutes(rule.StartMinutes % (24 * 60))).ToString("HH:mm");
        RuleEndBox.Text = TimeOnly.FromTimeSpan(TimeSpan.FromMinutes(rule.EndMinutes % (24 * 60))).ToString("HH:mm");
        RuleProfileBox.SelectedItem = _controller.FindProfile(rule.ProfileId);
        RulePriorityBox.Text = rule.Priority.ToString();
        RuleEnabledBox.IsChecked = rule.Enabled;
        foreach (var dayBox in RuleDayBoxes())
            dayBox.IsChecked = int.TryParse(dayBox.Tag?.ToString(), out var day) && rule.Weekdays.Contains(day);
    }

    private bool TryReadRuleEditor(ScheduleRule rule)
    {
        if (RuleProfileBox.SelectedItem is not Profile profile)
        {
            StatusText.Text = "Выберите профиль для правила.";
            return false;
        }
        if (!TryParseTime(RuleStartBox.Text, out var start) || !TryParseTime(RuleEndBox.Text, out var end))
        {
            StatusText.Text = "Укажите время в формате ЧЧ:ММ.";
            return false;
        }
        if (!int.TryParse(RulePriorityBox.Text, out var priority) || priority is < 0 or > 100)
        {
            StatusText.Text = "Приоритет должен быть числом от 0 до 100.";
            return false;
        }

        rule.Name = string.IsNullOrWhiteSpace(RuleNameBox.Text)
            ? $"Профиль {profile.Name}"
            : RuleNameBox.Text.Trim();
        rule.ProfileId = profile.Id;
        rule.StartMinutes = start;
        rule.EndMinutes = end;
        rule.Priority = priority;
        rule.Enabled = RuleEnabledBox.IsChecked == true;
        rule.Weekdays = RuleDayBoxes()
            .Where(dayBox => dayBox.IsChecked == true)
            .Select(dayBox => int.Parse(dayBox.Tag!.ToString()!))
            .ToList();
        return true;
    }

    private CheckBox[] RuleDayBoxes() =>
    [
        RuleMondayBox,
        RuleTuesdayBox,
        RuleWednesdayBox,
        RuleThursdayBox,
        RuleFridayBox,
        RuleSaturdayBox,
        RuleSundayBox
    ];

    private void SettingChanged(object? sender, RoutedEventArgs e)
    {
        if (sender is not CheckBox { Tag: string key, IsChecked: { } enabled })
            return;
        try
        {
            _config.Set(key, enabled);
            if (key == "start_with_system")
                AutostartService.Sync(enabled);
            if (key is "loop_videos" or "video_audio" or "interactive_web_wallpapers")
                _playback.UpdateRuntimeSettings();
            StatusText.Text = "Настройки сохранены.";
        }
        catch (Exception ex) when (
            ex is IOException or UnauthorizedAccessException or InvalidOperationException or NotSupportedException)
        {
            StatusText.Text = $"Не удалось сохранить настройку: {ex.Message}";
        }
    }

    private void StaticWallpaperModeSelectionChanged(object? sender, SelectionChangedEventArgs e)
    {
        if (_loadingSettings || GetComboTag(StaticWallpaperModeBox, "auto") is not { } mode)
            return;
        try
        {
            _config.Set("static_wallpaper_mode", mode);
            _playback.RefreshDisplayMode();
            StatusText.Text = "Режим статичных обоев сохранён.";
        }
        catch (Exception ex) when (
            ex is IOException or UnauthorizedAccessException or InvalidOperationException or
                NotSupportedException or Win32Exception)
        {
            StatusText.Text = $"Не удалось применить режим статичных обоев: {ex.Message}";
        }
    }

    private void LinuxWallpaperFitSelectionChanged(object? sender, SelectionChangedEventArgs e)
    {
        if (_loadingSettings || GetComboTag(LinuxWallpaperFitBox, "fill") is not { } fit)
            return;
        try
        {
            _config.Set("linux_wallpaper_fit", fit);
            _playback.RefreshDisplayMode();
            StatusText.Text = "Масштабирование системных обоев сохранено.";
        }
        catch (Exception ex) when (
            ex is IOException or UnauthorizedAccessException or InvalidOperationException or
                NotSupportedException or Win32Exception)
        {
            StatusText.Text = $"Не удалось применить масштабирование системных обоев: {ex.Message}";
        }
    }

    private void LoadSourceProviders()
    {
        var apiKeys = _config.Get("source_api_keys", new Dictionary<string, string>());
        _providers = WallpaperSources.BuildProviders(apiKeys);
        SourceProviderBox.ItemsSource = _providers;
        SourceProviderBox.ItemTemplate = new Avalonia.Controls.Templates.FuncDataTemplate<Provider>(
            (provider, _) => new TextBlock { Text = $"{provider.Label}{(provider.Enabled ? "" : " (нужен ключ)")}" },
            supportsRecycling: true);
        SourceProviderBox.SelectedItem = _providers.FirstOrDefault(provider => provider.Name == "bing") ?? _providers.FirstOrDefault();
        UpdateSourceKey();
    }

    private void UpdateSourceKey()
    {
        if (SourceProviderBox.SelectedItem is not Provider provider)
            return;
        var keys = _config.Get("source_api_keys", new Dictionary<string, string>());
        SourceKeyBox.Text = keys.TryGetValue(provider.Name, out var key) ? key : "";
        SourceKeyBox.IsEnabled = provider.KeyRequired;
    }

    private void SaveSourceKeyClick(object? sender, RoutedEventArgs e)
    {
        if (SourceProviderBox.SelectedItem is not Provider provider)
            return;
        if (!provider.KeyRequired)
        {
            StatusText.Text = $"{provider.Label} не требует API-ключ.";
            return;
        }

        try
        {
            var keys = _config.Get("source_api_keys", new Dictionary<string, string>());
            keys[provider.Name] = SourceKeyBox.Text?.Trim() ?? "";
            _config.Set("source_api_keys", keys);
            LoadSourceProviders();
            StatusText.Text = $"Ключ для {provider.Label} сохранён.";
        }
        catch (IOException ex)
        {
            StatusText.Text = $"Не удалось сохранить ключ: {ex.Message}";
        }
    }

    private async void SearchSourcesClick(object? sender, RoutedEventArgs e) =>
        await SearchSourcesAsync(daily: false);

    private async void DailySourcesClick(object? sender, RoutedEventArgs e) =>
        await SearchSourcesAsync(daily: true);

    private async Task SearchSourcesAsync(bool daily)
    {
        if (SourceProviderBox.SelectedItem is not Provider provider)
            return;

        _sourceSearchCancellation?.Cancel();
        _sourceSearchCancellation?.Dispose();
        _sourceSearchCancellation = new CancellationTokenSource(TimeSpan.FromSeconds(30));
        var cancellationToken = _sourceSearchCancellation.Token;
        try
        {
            StatusText.Text = $"Ищем в {provider.Label}…";
            var results = daily
                ? await provider.DailyAsync(cancellationToken)
                : await provider.SearchAsync(SourceQueryBox.Text?.Trim() ?? "", cancellationToken: cancellationToken);
            _sourceResults.Clear();
            foreach (var result in results)
                _sourceResults.Add(result);
            StatusText.Text = $"Найдено результатов: {results.Count}.";
        }
        catch (ProviderError ex)
        {
            StatusText.Text = $"Ошибка источника: {ex.Message}";
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            StatusText.Text = "Поиск отменён или превысил время ожидания.";
        }
        catch (HttpRequestException ex)
        {
            StatusText.Text = $"Ошибка сети: {ex.Message}";
        }
        catch (JsonException ex)
        {
            StatusText.Text = $"Не удалось разобрать ответ источника: {ex.Message}";
        }
        catch (IOException ex)
        {
            StatusText.Text = $"Ошибка поиска: {ex.Message}";
        }
    }

    private async void DownloadSourceClick(object? sender, RoutedEventArgs e)
    {
        if (SourceResultsList.SelectedItem is not SearchResult result)
            return;

        var destination = Path.Combine(DownloadDirectory, WallpaperSources.SuggestFilename(result));
        try
        {
            StatusText.Text = $"Скачиваем «{result.Title}»…";
            var downloaded = await WallpaperSources.DownloadAsync(result, destination);
            if (downloaded is null)
            {
                StatusText.Text = "Источник не смог скачать изображение.";
                return;
            }

            var wallpaper = _library.AddDownloaded(result, downloaded);
            RefreshWallpapers($"Обои «{wallpaper.Name}» добавлены в библиотеку.");
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or ProviderError)
        {
            StatusText.Text = $"Ошибка скачивания: {ex.Message}";
        }
    }

    private void StopClick(object? sender, RoutedEventArgs e)
    {
        try
        {
            _playback.ClearDesktop();
            StatusText.Text = "Обои сняты с рабочего стола.";
        }
        catch (Exception ex) when (ex is IOException or NotSupportedException or InvalidOperationException)
        {
            StatusText.Text = $"Не удалось очистить рабочий стол: {ex.Message}";
        }
    }

    private void PauseClick(object? sender, RoutedEventArgs e)
    {
        try
        {
            _controller.TogglePause();
        }
        catch (IOException ex)
        {
            StatusText.Text = $"Ошибка переключения паузы: {ex.Message}";
        }
    }

    private void PreviousClick(object? sender, RoutedEventArgs e)
    {
        try
        {
            _controller.Previous();
        }
        catch (Exception ex) when (ex is IOException or InvalidOperationException or NotSupportedException or Win32Exception)
        {
            StatusText.Text = $"Не удалось переключить обои: {ex.Message}";
        }
    }

    private void NextClick(object? sender, RoutedEventArgs e)
    {
        try
        {
            _controller.Next();
        }
        catch (Exception ex) when (ex is IOException or InvalidOperationException or NotSupportedException or Win32Exception)
        {
            StatusText.Text = $"Не удалось переключить обои: {ex.Message}";
        }
    }

    private void ProfileSelectionChanged(object? sender, SelectionChangedEventArgs e)
    {
        if (ProfilesList.SelectedItem is Profile profile)
        {
            ProfileEditNameBox.Text = profile.Name;
            ProfilePlaylistBox.SelectedItem = _controller.FindPlaylist(profile.PlaylistId);
            StartupProfileBox.SelectedItem =
                _controller.FindProfile(_config.Get("startup_profile_id", ""));
        }
        RefreshProfileDependentLists();
        UpdateProfileMonitorAssignment();
    }

    private void PlaylistSelectionChanged(object? sender, SelectionChangedEventArgs e)
    {
        var playlist = PlaylistsList.SelectedItem as Playlist;
        PlaylistIntervalBox.Text = playlist?.IntervalSeconds.ToString() ?? "";
        PlaylistShuffleBox.IsChecked = playlist?.Shuffle ?? false;
        PlaylistTransitionMsBox.Text = playlist?.TransitionMs.ToString() ?? "";
        SelectComboTag(PlaylistTransitionBox, playlist?.Transition ?? "fade");
        RefreshPlaylistItems(playlist);
        StatusText.Text = playlist is null
            ? "Готово"
            : $"Обоев в плейлисте: {playlist.Items.Count}; интервал: {playlist.IntervalSeconds} сек.";
    }

    private void SavePlaylistClick(object? sender, RoutedEventArgs e)
    {
        if (PlaylistsList.SelectedItem is not Playlist playlist)
        {
            StatusText.Text = "Сначала выберите или создайте плейлист.";
            return;
        }
        if (!int.TryParse(PlaylistIntervalBox.Text, out var interval) || interval < 0)
        {
            StatusText.Text = "Интервал должен быть неотрицательным числом секунд.";
            return;
        }

        try
        {
            if (WallpaperList.SelectedItem is Wallpaper selected && !playlist.Items.Contains(selected.Id))
                playlist.Items.Add(selected.Id);
            _controller.SavePlaylist(playlist);
            RefreshPlaylistItems(playlist);
            StatusText.Text = $"Плейлист «{playlist.Name}» обновлён.";
        }
        catch (Exception ex) when (ex is IOException or InvalidOperationException or NotSupportedException or Win32Exception)
        {
            StatusText.Text = $"Не удалось добавить обои в плейлист: {ex.Message}";
        }
    }

    private void SavePlaylistSettingsClick(object? sender, RoutedEventArgs e)
    {
        if (PlaylistsList.SelectedItem is not Playlist playlist)
        {
            StatusText.Text = "Сначала выберите плейлист.";
            return;
        }
        if (!int.TryParse(PlaylistIntervalBox.Text, out var interval) || interval < 0)
        {
            StatusText.Text = "Интервал должен быть неотрицательным числом секунд.";
            return;
        }
        if (!int.TryParse(PlaylistTransitionMsBox.Text, out var transitionMs) ||
            transitionMs is < 0 or > 5000)
        {
            StatusText.Text = "Длительность перехода должна быть от 0 до 5000 мс.";
            return;
        }

        try
        {
            playlist.IntervalSeconds = interval;
            playlist.Shuffle = PlaylistShuffleBox.IsChecked == true;
            playlist.Transition = GetComboTag(PlaylistTransitionBox, "fade");
            playlist.TransitionMs = transitionMs;
            SavePlaylist(playlist);
            StatusText.Text = $"Настройки плейлиста «{playlist.Name}» сохранены.";
        }
        catch (Exception ex) when (
            ex is IOException or InvalidOperationException or NotSupportedException or Win32Exception)
        {
            StatusText.Text = $"Не удалось сохранить настройки плейлиста: {ex.Message}";
        }
    }

    private void MovePlaylistItemUpClick(object? sender, RoutedEventArgs e) =>
        MoveSelectedPlaylistItems(-1);

    private void MovePlaylistItemDownClick(object? sender, RoutedEventArgs e) =>
        MoveSelectedPlaylistItems(1);

    private void MoveSelectedPlaylistItems(int direction)
    {
        if (PlaylistsList.SelectedItem is not Playlist playlist ||
            PlaylistItemsList.SelectedItem is not Wallpaper wallpaper)
            return;
        var index = playlist.Items.IndexOf(wallpaper.Id);
        var target = index + direction;
        if (index < 0 || target < 0 || target >= playlist.Items.Count)
            return;

        (playlist.Items[index], playlist.Items[target]) =
            (playlist.Items[target], playlist.Items[index]);
        try
        {
            _controller.SavePlaylist(playlist);
            RefreshPlaylistItems(playlist);
            PlaylistItemsList.SelectedItem = _library.Get(wallpaper.Id);
            StatusText.Text = $"Порядок плейлиста «{playlist.Name}» изменён.";
        }
        catch (Exception ex) when (
            ex is IOException or InvalidOperationException or NotSupportedException or Win32Exception)
        {
            StatusText.Text = $"Не удалось изменить порядок: {ex.Message}";
        }
    }

    private void RemovePlaylistItemsClick(object? sender, RoutedEventArgs e)
    {
        if (PlaylistsList.SelectedItem is not Playlist playlist)
            return;
        var selectedItems = PlaylistItemsList.SelectedItems;
        if (selectedItems is null)
            return;
        var selected = selectedItems.OfType<Wallpaper>()
            .Select(wallpaper => wallpaper.Id).ToHashSet(StringComparer.Ordinal);
        if (selected.Count == 0)
        {
            StatusText.Text = "Выберите обои, которые нужно убрать из плейлиста.";
            return;
        }

        playlist.Items.RemoveAll(id => selected.Contains(id));
        try
        {
            _controller.SavePlaylist(playlist);
            RefreshPlaylistItems(playlist);
            StatusText.Text = $"Удалено из плейлиста: {selected.Count}.";
        }
        catch (Exception ex) when (
            ex is IOException or InvalidOperationException or NotSupportedException or Win32Exception)
        {
            StatusText.Text = $"Не удалось сохранить изменения плейлиста: {ex.Message}";
        }
    }

    private void SavePlaylist(Playlist playlist)
    {
        playlist.IntervalSeconds = int.Parse(PlaylistIntervalBox.Text!);
        playlist.Shuffle = PlaylistShuffleBox.IsChecked == true;
        playlist.Transition = GetComboTag(PlaylistTransitionBox, "fade");
        playlist.TransitionMs = int.Parse(PlaylistTransitionMsBox.Text!);
        _controller.SavePlaylist(playlist);
        RefreshPlaylistItems(playlist);
    }

    private void RefreshPlaylistItems(Playlist? playlist) =>
        PlaylistItemsList.ItemsSource = playlist?.Items
            .Select(id => _library.Get(id))
            .OfType<Wallpaper>()
            .ToArray();

    private void RemovePlaylistClick(object? sender, RoutedEventArgs e)
    {
        if (PlaylistsList.SelectedItem is not Playlist playlist)
            return;
        try
        {
            if (!_controller.RemovePlaylist(playlist.Id))
                return;
            RefreshPlaylists();
            RefreshProfiles();
            StatusText.Text = $"Плейлист «{playlist.Name}» удалён.";
        }
        catch (Exception ex) when (ex is IOException or InvalidOperationException or NotSupportedException or Win32Exception)
        {
            StatusText.Text = $"Не удалось удалить плейлист: {ex.Message}";
        }
    }

    private void RefreshLibraryDirectories()
    {
        _libraryDirectories.Clear();
        _libraryDirectories.Add(WallflowPaths.WallpaperDirectory);
        foreach (var directory in _config.LibraryDirectories)
            _libraryDirectories.Add(directory);
    }

    private void UpdateDownloadFolder() =>
        DownloadFolderBox.Text = _config.Get("download_folder", "");

    private string DownloadDirectory
    {
        get
        {
            var configured = _config.Get("download_folder", "");
            if (!string.IsNullOrWhiteSpace(configured))
            {
                try
                {
                    Directory.CreateDirectory(configured);
                    return configured;
                }
                catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
                {
                    Log.Warn($"Could not use the configured download folder {configured}", exception);
                }
            }

            Directory.CreateDirectory(WallflowPaths.CacheDirectory);
            return WallflowPaths.CacheDirectory;
        }
    }

    private void LoadSettings()
    {
        _loadingSettings = true;
        PauseFullscreenBox.Tag = "pause_on_fullscreen";
        PauseLockBox.Tag = "pause_on_lock";
        PauseBatteryBox.Tag = "pause_on_battery";
        PauseRemoteBox.Tag = "pause_on_remote_session";
        PauseIdleBox.Tag = "pause_when_idle";
        StartWithSystemBox.Tag = "start_with_system";
        MinimizeToTrayBox.Tag = "minimize_to_tray";
        CloseToTrayBox.Tag = "close_to_tray";
        LoopVideosBox.Tag = "loop_videos";
        VideoAudioBox.Tag = "video_audio";
        InteractiveWebBox.Tag = "interactive_web_wallpapers";
        PauseFullscreenBox.IsChecked = _config.Get("pause_on_fullscreen", true);
        PauseLockBox.IsChecked = _config.Get("pause_on_lock", true);
        PauseBatteryBox.IsChecked = _config.Get("pause_on_battery", false);
        PauseRemoteBox.IsChecked = _config.Get("pause_on_remote_session", true);
        PauseIdleBox.IsChecked = _config.Get("pause_when_idle", false);
        StartWithSystemBox.IsChecked = _config.Get("start_with_system", false);
        MinimizeToTrayBox.IsChecked = _config.Get("minimize_to_tray", true);
        CloseToTrayBox.IsChecked = _config.Get("close_to_tray", true);
        SelectComboTag(StaticWallpaperModeBox, _config.Get("static_wallpaper_mode", "auto"));
        SelectComboTag(LinuxWallpaperFitBox, _config.Get("linux_wallpaper_fit", "fill"));
        BatteryThresholdBox.Text = _config.Get("battery_pause_threshold", 20).ToString();
        IdleMinutesBox.Text = _config.Get("idle_minutes", 5).ToString();
        MaxFpsBox.Text = _config.Get("max_fps", 30).ToString();
        LoopVideosBox.IsChecked = _config.Get("loop_videos", true);
        VideoAudioBox.IsChecked = _config.Get("video_audio", false);
        InteractiveWebBox.IsChecked = _config.Get("interactive_web_wallpapers", true);
        SelectComboTag(AccentColorBox, _config.Get("accent_color", "auto"));
        SelectComboTag(ThumbSizeBox, _config.Get("thumb_size", 220).ToString());
        RefreshLibraryDirectories();
        UpdateDownloadFolder();
        AboutText.Text =
            $"Wallflow 1.0.0 · MIT License\nЖурнал: {Log.LogFile}\n" +
            $"Данные: {WallflowPaths.DataDirectory}\nМиниатюры: {WallflowPaths.ThumbnailDirectory}";
        try
        {
            AutostartService.Sync(StartWithSystemBox.IsChecked == true);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or InvalidOperationException)
        {
            StatusText.Text = $"Не удалось синхронизировать автозапуск: {ex.Message}";
        }
        finally
        {
            _loadingSettings = false;
        }
    }

    private void SettingNumberLostFocus(object? sender, RoutedEventArgs e)
    {
        if (sender is not TextBox { Name: { } name, Text: { } text })
            return;

        var (key, fallback, minimum, maximum) = name switch
        {
            "BatteryThresholdBox" => ("battery_pause_threshold", 20, 0, 100),
            "IdleMinutesBox" => ("idle_minutes", 5, 1, 120),
            "MaxFpsBox" => ("max_fps", 30, 5, 120),
            _ => throw new InvalidOperationException($"Unknown numeric setting control: {name}")
        };
        if (!int.TryParse(text, out var value) || value < minimum || value > maximum)
        {
            var invalidBox = (TextBox)sender;
            invalidBox.Text = _config.Get(key, fallback).ToString();
            StatusText.Text = $"Введите целое число от {minimum} до {maximum}.";
            return;
        }

        try
        {
            _config.Set(key, value);
            if (key == "max_fps")
                _playback.UpdateRuntimeSettings();
            StatusText.Text = "Настройки сохранены.";
        }
        catch (Exception ex) when (
            ex is IOException or UnauthorizedAccessException or InvalidOperationException or NotSupportedException)
        {
            StatusText.Text = $"Не удалось сохранить настройку: {ex.Message}";
        }
    }

    private void RefreshProfiles(Profile? select = null)
    {
        var selectedId = select?.Id ?? (ProfilesList.SelectedItem as Profile)?.Id;
        ProfilesList.ItemsSource = null;
        ProfilesList.ItemsSource = _controller.Profiles;
        StartupProfileBox.ItemsSource = null;
        StartupProfileBox.ItemsSource = _controller.Profiles;
        ProfilePlaylistBox.ItemsSource = null;
        ProfilePlaylistBox.ItemsSource = _controller.Playlists;
        RuleProfileBox.ItemsSource = null;
        RuleProfileBox.ItemsSource = _controller.Profiles;
        if (selectedId is not null)
            ProfilesList.SelectedItem = _controller.FindProfile(selectedId);
    }

    private void RefreshProfileDependentLists()
    {
        if (RulesList is null)
            return;
        var selectedId = (RulesList.SelectedItem as ScheduleRule)?.Id;
        RulesList.ItemsSource = null;
        RulesList.ItemsSource = _controller.Rules;
        if (selectedId is not null)
            RulesList.SelectedItem = _controller.Rules.FirstOrDefault(rule => rule.Id == selectedId);
    }

    private void RefreshPlaylists(Playlist? select = null)
    {
        var selectedId = select?.Id ?? (PlaylistsList.SelectedItem as Playlist)?.Id;
        PlaylistsList.ItemsSource = null;
        PlaylistsList.ItemsSource = _controller.Playlists;
        if (selectedId is not null)
            PlaylistsList.SelectedItem = _controller.Playlists.FirstOrDefault(item => item.Id == selectedId);
    }

    private void RefreshRules(ScheduleRule? select = null)
    {
        var selectedId = select?.Id ?? (RulesList.SelectedItem as ScheduleRule)?.Id;
        RulesList.ItemsSource = null;
        RulesList.ItemsSource = _controller.Rules;
        if (selectedId is not null)
            RulesList.SelectedItem = _controller.Rules.FirstOrDefault(item => item.Id == selectedId);
    }

    private static bool TryParseTime(string? value, out int minutes)
    {
        minutes = 0;
        if (!TimeOnly.TryParse(value, out var time))
            return false;
        minutes = time.Hour * 60 + time.Minute;
        return true;
    }

    protected override void OnClosed(EventArgs e)
    {
        _sourceSearchCancellation?.Cancel();
        _sourceSearchCancellation?.Dispose();
        _controller.Dispose();
        base.OnClosed(e);
    }

    protected override void OnOpened(EventArgs e)
    {
        base.OnOpened(e);
        if (Environment.GetCommandLineArgs().Contains("--minimized", StringComparer.Ordinal))
            WindowState = WindowState.Minimized;
    }

    private void RefreshWallpapers(string? status = null)
    {
        var previousStatus = status ?? StatusText?.Text ?? "Готово";
        var search = SearchBox?.Text ?? "";
        var selectedId = (WallpaperList?.SelectedItem as Wallpaper)?.Id;
        var kind = GetComboTag(WallpaperKindFilterBox, "all");
        var kinds = Enum.TryParse<WallpaperKind>(kind, ignoreCase: true, out var selectedKind)
            ? new[] { selectedKind }
            : null;
        var items = _library.Filter(search, kinds, FavouritesOnlyBox?.IsChecked == true);

        _visibleWallpapers.Clear();
        foreach (var item in items)
        {
            _visibleWallpapers.Add(item);
            _thumbnails.Attach(item);
        }

        if (selectedId is not null && WallpaperList is { } list)
            list.SelectedItem = _visibleWallpapers.FirstOrDefault(item => item.Id == selectedId);

        if (FavoriteButton is not null)
            FavoriteButton.Content = (WallpaperList?.SelectedItem as Wallpaper)?.Favourite == true
                ? "Убрать из избранного"
                : "В избранное";

        if (StatusText is not null)
            StatusText.Text = previousStatus;
    }
}

using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json;
using Avalonia.Threading;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Platform;

namespace Wallflow.App.Core;

public interface IWallpaperPlayback
{
    IReadOnlyList<MonitorInfo> Monitors { get; }
    Wallpaper? Current { get; }
    bool Paused { get; }
    event EventHandler<Wallpaper?>? WallpaperChanged;
    event EventHandler<bool>? PauseChanged;
    event EventHandler<string>? StatusMessage;
    void SetCurrent(Wallpaper? wallpaper, IReadOnlyDictionary<string, Wallpaper?>? monitors = null, int transitionMs = 800);
    void SetPaused(bool paused);
    bool ApplyAccent(string color);
}

public sealed class DesktopWallpaperPlayback : IWallpaperPlayback, IDisposable
{
    private readonly IWallpaperBackend _backend;
    private readonly Dictionary<string, WallpaperSurfaceWindow> _surfaces = new(StringComparer.Ordinal);
    private readonly WallflowConfig? _config;
    private IReadOnlyDictionary<string, Wallpaper?>? _monitorAssignments;
    private Screens? _subscribedScreens;
    private bool _usesSurfaces;
    private bool _disposed;

    public DesktopWallpaperPlayback(IWallpaperBackend? backend = null, WallflowConfig? config = null)
    {
        _backend = backend ?? WallpaperBackendFactory.Create();
        _config = config;
    }

    public IReadOnlyList<MonitorInfo> Monitors => GetScreens().Select(ToMonitorInfo).ToArray();
    public Wallpaper? Current { get; private set; }
    public bool Paused { get; private set; }
    public event EventHandler<Wallpaper?>? WallpaperChanged;
    public event EventHandler<bool>? PauseChanged;
    public event EventHandler<string>? StatusMessage;

    public void SetCurrent(
        Wallpaper? wallpaper,
        IReadOnlyDictionary<string, Wallpaper?>? monitors = null,
        int transitionMs = 800)
    {
        SubscribeToScreenChanges();
        var screens = GetScreens();
        var staticMode = _config?.Get("static_wallpaper_mode", "auto") ?? "auto";
        var needsSurface = monitors is { Count: > 0 } ||
                           wallpaper is not null &&
                           (wallpaper.WallpaperType != WallpaperKind.Image ||
                            staticMode == "window" ||
                            Path.GetExtension(wallpaper.Path).Equals(".gif", StringComparison.OrdinalIgnoreCase));
        if (needsSurface)
        {
            ApplySurfaces(screens, wallpaper, monitors, transitionMs);
        }
        else
        {
            var fit = _config?.Get("linux_wallpaper_fit", wallpaper?.DisplayFit ?? "fill")
                ?? wallpaper?.DisplayFit
                ?? "fill";
            if (wallpaper is not null && !_backend.SetWallpaper(wallpaper.Path, fit))
                throw new IOException($"The {_backend.Name} desktop wallpaper API rejected {wallpaper.Path}.");
            CloseSurfaces();
        }

        _monitorAssignments = monitors is null
            ? null
            : new Dictionary<string, Wallpaper?>(monitors, StringComparer.Ordinal);
        _usesSurfaces = needsSurface;
        Current = wallpaper;
        WallpaperChanged?.Invoke(this, wallpaper);
        if (wallpaper is not null)
            StatusMessage?.Invoke(this, $"Wallpaper applied: {wallpaper.Name}");
    }

    public void RefreshDisplayMode()
    {
        if (Current is not null)
            SetCurrent(Current, _monitorAssignments, transitionMs: 0);
    }

    public void SetPaused(bool paused)
    {
        if (Paused == paused)
            return;
        Paused = paused;
        foreach (var surface in _surfaces.Values)
        {
            if (paused)
                surface.PauseWallpaper();
            else
                surface.ResumeWallpaper();
        }
        PauseChanged?.Invoke(this, paused);
    }

    public void UpdateRuntimeSettings()
    {
        var loopVideos = _config?.Get("loop_videos", true) ?? true;
        var videoAudio = _config?.Get("video_audio", false) ?? false;
        var interactiveWeb = _config?.Get("interactive_web_wallpapers", true) ?? true;
        var maxFps = _config?.Get("max_fps", 30) ?? 30;
        foreach (var surface in _surfaces.Values)
            surface.UpdateSettings(loopVideos, videoAudio, interactiveWeb, maxFps);
    }

    public bool ApplyAccent(string color) => false;

    public void Dispose()
    {
        _disposed = true;
        if (_subscribedScreens is not null)
            _subscribedScreens.Changed -= OnScreensChanged;
        _subscribedScreens = null;
        CloseSurfaces();
    }

    private void ApplySurfaces(
        IReadOnlyList<Screen> screens,
        Wallpaper? wallpaper,
        IReadOnlyDictionary<string, Wallpaper?>? assignments,
        int transitionMs)
    {
        if (screens.Count == 0)
            throw new NotSupportedException("No display surfaces are available for live wallpapers.");

        var virtualBounds = new PixelRect(
            screens.Min(screen => screen.Bounds.X),
            screens.Min(screen => screen.Bounds.Y),
            screens.Max(screen => screen.Bounds.Right) - screens.Min(screen => screen.Bounds.X),
            screens.Max(screen => screen.Bounds.Bottom) - screens.Min(screen => screen.Bounds.Y));
        var newSurfaces = new Dictionary<string, WallpaperSurfaceWindow>(StringComparer.Ordinal);
        try
        {
            for (var index = 0; index < screens.Count; index++)
            {
                var screen = screens[index];
                var monitor = ToMonitorInfo(screen, index);
                var selected = assignments is not null &&
                               assignments.TryGetValue(monitor.Id, out var assigned) &&
                               assigned is not null
                    ? assigned
                    : wallpaper;
                if (selected is null)
                    continue;

                var surface = new WallpaperSurfaceWindow(
                    screen,
                    monitor,
                    selected,
                    _config?.Get("loop_videos", true) ?? true,
                    _config?.Get("video_audio", false) ?? false,
                    transitionMs,
                    _config?.Get("interactive_web_wallpapers", true) ?? true,
                    message => StatusMessage?.Invoke(this, message),
                    _config?.Get("max_fps", 30) ?? 30);
                surface.Show();
                if (!surface.AttachToDesktop(screen.Bounds, virtualBounds))
                {
                    surface.Close();
                    throw new NotSupportedException(
                        $"Desktop-level wallpaper surfaces are not supported on this {RuntimeInformation.OSDescription} display session.");
                }
                if (Paused)
                    surface.PauseWallpaper();
                newSurfaces.Add(monitor.Id, surface);
            }
        }
        catch
        {
            foreach (var surface in newSurfaces.Values)
                surface.Close();
            throw;
        }

        CloseSurfaces();
        foreach (var (id, surface) in newSurfaces)
            _surfaces.Add(id, surface);
    }

    private void CloseSurfaces()
    {
        foreach (var surface in _surfaces.Values)
            surface.Close();
        _surfaces.Clear();
    }

    private void SubscribeToScreenChanges()
    {
        if (_subscribedScreens is not null ||
            Application.Current?.ApplicationLifetime is not IClassicDesktopStyleApplicationLifetime desktop ||
            desktop.MainWindow is not { } mainWindow)
            return;

        _subscribedScreens = mainWindow.Screens;
        _subscribedScreens.Changed += OnScreensChanged;
    }

    private void OnScreensChanged(object? sender, EventArgs e)
    {
        if (_disposed || !_usesSurfaces)
            return;

        try
        {
            ApplySurfaces(GetScreens(), Current, _monitorAssignments, transitionMs: 0);
            StatusMessage?.Invoke(this, "Display configuration changed; wallpaper surfaces were refreshed.");
        }
        catch (Exception exception) when (
            exception is IOException or InvalidOperationException or NotSupportedException or
                System.ComponentModel.Win32Exception)
        {
            StatusMessage?.Invoke(this, $"Could not refresh wallpaper surfaces: {exception.Message}");
        }
    }

    private static IReadOnlyList<Screen> GetScreens() =>
        Application.Current?.ApplicationLifetime is IClassicDesktopStyleApplicationLifetime desktop &&
        desktop.MainWindow is { } mainWindow
            ? mainWindow.Screens.All.ToArray()
            : [];

    private static MonitorInfo ToMonitorInfo(Screen screen, int index = 0)
    {
        var id = screen.DisplayName ?? $"monitor-{index + 1}";
        return new MonitorInfo
        {
            Id = id,
            Name = screen.DisplayName ?? $"Monitor {index + 1}",
            Width = screen.Bounds.Width,
            Height = screen.Bounds.Height,
            X = screen.Bounds.X,
            Y = screen.Bounds.Y,
            Scale = screen.Scaling,
            Primary = screen.IsPrimary,
            Identifier = id
        };
    }
}

public sealed class PlaybackController : IDisposable
{
    private static readonly TimeSpan PolicyInterval = TimeSpan.FromSeconds(2);
    private static readonly TimeSpan RuleInterval = TimeSpan.FromMinutes(1);

    private readonly WallflowConfig _config;
    private readonly WallpaperLibrary _library;
    private readonly IWallpaperPlayback _player;
    private readonly DispatcherTimer _policyTimer;
    private readonly DispatcherTimer _ruleTimer;
    private readonly Random _random = new();
    private DateTimeOffset? _advanceAt;
    private string _lastRuleId = "";
    private bool _manualPause;
    private PauseReason? _activePauseReason;
    private bool _disposed;

    private List<Profile> _profiles;
    private List<Playlist> _playlists;
    private List<ScheduleRule> _rules;
    private int _playlistPosition = -1;

    public PlaybackController(WallflowConfig config, WallpaperLibrary library, IWallpaperPlayback player)
    {
        _config = config;
        _library = library;
        _player = player;
        _profiles = config.LoadProfiles();
        _playlists = config.LoadPlaylists();
        _rules = config.LoadRules();

        _policyTimer = new DispatcherTimer { Interval = PolicyInterval };
        _policyTimer.Tick += (_, _) => EvaluatePolicy();
        _ruleTimer = new DispatcherTimer { Interval = RuleInterval };
        _ruleTimer.Tick += (_, _) => EvaluateRules();

        _player.WallpaperChanged += (_, wallpaper) => WallpaperChanged?.Invoke(this, wallpaper);
        _player.StatusMessage += (_, message) => StatusMessage?.Invoke(this, message);
    }

    public event EventHandler<Profile?>? ProfileChanged;
    public event EventHandler<Wallpaper?>? WallpaperChanged;
    public event EventHandler<bool>? PauseChanged;
    public event EventHandler? PlaylistChanged;
    public event EventHandler<string>? StatusMessage;

    public IReadOnlyList<Profile> Profiles => _profiles;
    public IReadOnlyList<Playlist> Playlists => _playlists;
    public IReadOnlyList<ScheduleRule> Rules => _rules;
    public Profile? ActiveProfile { get; private set; }
    public Playlist? ActivePlaylist { get; private set; }
    public bool Paused => _activePauseReason.HasValue;
    public PauseReason? ActivePauseReason => _activePauseReason;
    public Wallpaper? CurrentWallpaper => _player.Current;
    public IReadOnlyList<MonitorInfo> Monitors => _player.Monitors;

    public void Start()
    {
        ThrowIfDisposed();
        if (_profiles.Count == 0)
        {
            _profiles.Add(new Profile { Name = "По умолчанию" });
            SaveProfiles();
        }

        _policyTimer.Start();
        _ruleTimer.Start();
        EvaluateRules(force: true);

        if (_lastRuleId.Length == 0)
        {
            var wanted = _config.Get("startup_profile_id", "") is { Length: > 0 } startup
                ? startup
                : _config.Get("last_profile_id", "");
            var profile = FindProfile(wanted) ?? _profiles[0];
            ActivateProfile(profile.Id, remember: false);
        }
        EvaluatePolicy(force: true);
    }

    public Profile? FindProfile(string? id) =>
        string.IsNullOrEmpty(id) ? null : _profiles.FirstOrDefault(item => item.Id == id);

    public Playlist? FindPlaylist(string? id) =>
        string.IsNullOrEmpty(id) ? null : _playlists.FirstOrDefault(item => item.Id == id);

    public Wallpaper? LibraryItem(string? id) => string.IsNullOrEmpty(id) ? null : _library.Get(id);

    public Profile AddProfile(string name, string? wallpaperId = null)
    {
        var profile = new Profile { Name = name.Trim(), WallpaperId = wallpaperId };
        if (profile.Name.Length == 0)
            throw new ArgumentException("Profile name cannot be empty.", nameof(name));
        _profiles.Add(profile);
        SaveProfiles();
        return profile;
    }

    public Profile DuplicateProfile(string profileId)
    {
        var source = FindProfile(profileId) ??
            throw new ArgumentException($"Profile not found: {profileId}", nameof(profileId));
        var copy = new Profile
        {
            Name = $"{source.Name} (копия)",
            WallpaperId = source.WallpaperId,
            PlaylistId = source.PlaylistId,
            AccentColor = source.AccentColor,
            Monitors = source.Monitors.Select(item => new MonitorAssignment
            {
                MonitorId = item.MonitorId,
                WallpaperId = item.WallpaperId,
                Fit = item.Fit,
                Alignment = item.Alignment
            }).ToList()
        };
        _profiles.Add(copy);
        SaveProfiles();
        return copy;
    }

    public void RenameProfile(string profileId, string name)
    {
        var profile = FindProfile(profileId) ??
            throw new ArgumentException($"Profile not found: {profileId}", nameof(profileId));
        if (profile.Locked)
            throw new InvalidOperationException($"Profile is locked: {profile.Name}");
        var normalizedName = name.Trim();
        if (normalizedName.Length == 0)
            throw new ArgumentException("Profile name cannot be empty.", nameof(name));
        profile.Name = normalizedName;
        SaveProfiles();
        if (ActiveProfile?.Id == profile.Id)
            ProfileChanged?.Invoke(this, profile);
    }

    public void SetProfilePlaylist(string profileId, string? playlistId)
    {
        var profile = FindProfile(profileId) ??
            throw new ArgumentException($"Profile not found: {profileId}", nameof(profileId));
        var playlist = playlistId is null ? null : FindPlaylist(playlistId) ??
            throw new ArgumentException($"Playlist not found: {playlistId}", nameof(playlistId));
        profile.PlaylistId = playlist?.Id;
        if (playlist is not null)
            profile.WallpaperId = null;
        SaveProfiles();
        if (ActiveProfile?.Id == profile.Id)
        {
            ActivePlaylist = playlist;
            _playlistPosition = -1;
            ApplyNow();
            ProfileChanged?.Invoke(this, profile);
        }
    }

    public bool RemoveProfile(string id)
    {
        if (_profiles.Count <= 1)
            return false;
        var profile = FindProfile(id);
        if (profile is null || profile.Locked)
            return false;

        _profiles.Remove(profile);
        SaveProfiles();
        if (ActiveProfile?.Id == id)
            ActivateProfile(_profiles[0].Id, remember: false);
        return true;
    }

    public void SetProfileWallpaper(string profileId, string? wallpaperId)
    {
        var profile = FindProfile(profileId) ??
            throw new ArgumentException($"Profile not found: {profileId}", nameof(profileId));
        if (wallpaperId is not null && _library.Get(wallpaperId) is null)
            throw new ArgumentException($"Wallpaper not found in library: {wallpaperId}", nameof(wallpaperId));

        profile.WallpaperId = wallpaperId;
        if (wallpaperId is not null)
            profile.PlaylistId = null;
        SaveProfiles();
        if (ActiveProfile?.Id == profile.Id)
        {
            ActivePlaylist = FindPlaylist(profile.PlaylistId);
            _playlistPosition = -1;
            ApplyNow();
        }
    }

    public bool ActivateProfile(string id, bool remember = true, bool announce = true)
    {
        var profile = FindProfile(id);
        if (profile is null)
            return false;

        ActiveProfile = profile;
        ActivePlaylist = FindPlaylist(profile.PlaylistId);
        _playlistPosition = -1;
        if (remember)
            _config.Set("last_profile_id", profile.Id);

        ApplyNow();
        ProfileChanged?.Invoke(this, profile);
        if (announce)
            StatusMessage?.Invoke(this, $"Профиль: {profile.Name}");
        return true;
    }

    public Playlist AddPlaylist(string name, IEnumerable<string>? items = null)
    {
        var playlist = new Playlist { Name = name.Trim(), Items = items?.ToList() ?? [] };
        if (playlist.Name.Length == 0)
            throw new ArgumentException("Playlist name cannot be empty.", nameof(name));
        _playlists.Add(playlist);
        SavePlaylists();
        return playlist;
    }

    public bool RemovePlaylist(string id)
    {
        var playlist = FindPlaylist(id);
        if (playlist is null)
            return false;
        _playlists.Remove(playlist);
        foreach (var profile in _profiles.Where(item => item.PlaylistId == id))
            profile.PlaylistId = null;
        SavePlaylists();
        SaveProfiles();
        if (ActivePlaylist?.Id == id)
        {
            ActivePlaylist = null;
            _playlistPosition = -1;
            ApplyNow();
        }
        return true;
    }

    public void SavePlaylist(Playlist playlist)
    {
        if (!_playlists.Contains(playlist))
            throw new ArgumentException("The playlist does not belong to this controller.", nameof(playlist));
        if (playlist.IntervalSeconds < 0)
            throw new ArgumentOutOfRangeException(nameof(playlist), "Playlist interval cannot be negative.");
        SavePlaylists();
        if (ActivePlaylist?.Id == playlist.Id)
            _advanceAt = !playlist.Paused && playlist.IntervalSeconds > 0
                ? DateTimeOffset.UtcNow.AddSeconds(playlist.IntervalSeconds)
                : null;
    }

    public void SetPlaylist(string? id)
    {
        if (id is not null && FindPlaylist(id) is null)
            throw new ArgumentException($"Playlist not found: {id}", nameof(id));
        if (ActiveProfile is null)
            return;
        ActiveProfile.PlaylistId = id;
        ActivePlaylist = FindPlaylist(id);
        _playlistPosition = -1;
        SaveProfiles();
        ApplyNow();
        PlaylistChanged?.Invoke(this, EventArgs.Empty);
    }

    public void SetWallpaper(string? id)
    {
        if (id is not null && _library.Get(id) is null)
            throw new ArgumentException($"Wallpaper not found in library: {id}", nameof(id));
        if (ActiveProfile is null)
            return;
        ActiveProfile.WallpaperId = id;
        if (id is not null)
        {
            ActiveProfile.PlaylistId = null;
            ActivePlaylist = null;
        }
        SaveProfiles();
        ApplyNow();
    }

    public void SetMonitorWallpaper(
        string monitorId,
        string? wallpaperId,
        string? fit = null,
        string? alignment = null)
    {
        if (wallpaperId is not null && _library.Get(wallpaperId) is null)
            throw new ArgumentException($"Wallpaper not found in library: {wallpaperId}", nameof(wallpaperId));
        if (ActiveProfile is null)
            return;
        ActiveProfile.SetMonitor(monitorId, wallpaperId, fit, alignment);
        SaveProfiles();
        ApplyNow();
    }

    public void Next() => Step(1);
    public void Previous() => Step(-1);

    public void Step(int delta)
    {
        if (ActivePlaylist is not { Items.Count: > 0 } playlist)
            return;
        _playlistPosition = ((_playlistPosition + delta) % playlist.Items.Count + playlist.Items.Count) % playlist.Items.Count;
        ApplyNow();
        PlaylistChanged?.Invoke(this, EventArgs.Empty);
    }

    public void JumpTo(int index)
    {
        if (ActivePlaylist is not { } playlist || index < 0 || index >= playlist.Items.Count)
            return;
        _playlistPosition = index;
        ApplyNow();
        PlaylistChanged?.Invoke(this, EventArgs.Empty);
    }

    public void SetManualPause(bool paused)
    {
        if (_manualPause == paused)
            return;
        _manualPause = paused;
        EvaluatePolicy(force: true);
    }

    public void TogglePause() => SetManualPause(!_manualPause);

    public void ResumeNow()
    {
        _advanceAt = null;
        Step(1);
    }

    public void AddRule(ScheduleRule rule)
    {
        ArgumentNullException.ThrowIfNull(rule);
        _rules.Add(rule);
        SaveRules();
        EvaluateRules();
    }

    public void UpdateRule(ScheduleRule rule)
    {
        ArgumentNullException.ThrowIfNull(rule);
        if (!_rules.Contains(rule))
            throw new ArgumentException("The schedule rule does not belong to this controller.", nameof(rule));
        if (rule.StartMinutes is < 0 or >= 24 * 60 ||
            rule.EndMinutes is < 0 or > 24 * 60 ||
            rule.Weekdays.Any(day => day is < 0 or > 6))
            throw new ArgumentOutOfRangeException(nameof(rule), "Schedule rule values are out of range.");
        if (FindProfile(rule.ProfileId) is null)
            throw new ArgumentException($"Profile not found: {rule.ProfileId}", nameof(rule));

        SaveRules();
        _lastRuleId = "";
        EvaluateRules(force: true);
    }

    public bool RemoveRule(string id)
    {
        var removed = _rules.RemoveAll(rule => rule.Id == id) > 0;
        if (removed)
        {
            SaveRules();
            EvaluateRules();
        }
        return removed;
    }

    public void SaveProfiles() => _config.SaveProfiles(_profiles);
    public void SavePlaylists() => _config.SavePlaylists(_playlists);
    public void SaveRules() => _config.SaveRules(_rules);

    private void ApplyNow()
    {
        var profile = ActiveProfile;
        if (profile is null)
            return;

        var monitorMap = profile.Monitors
            .Where(assignment => !string.IsNullOrEmpty(assignment.WallpaperId))
            .Select(assignment => (
                assignment.MonitorId,
                Assignment: assignment,
                Wallpaper: _library.Get(assignment.WallpaperId!)))
            .Where(item => item.Wallpaper is not null)
            .ToDictionary(
                item => item.MonitorId,
                item => (Wallpaper?)item.Wallpaper!.ForDisplay(item.Assignment.Fit, item.Assignment.Alignment));

        Wallpaper? wallpaper;
        if (ActivePlaylist is { Items.Count: > 0 } playlist)
        {
            if (_playlistPosition < 0 || _playlistPosition >= playlist.Items.Count)
                _playlistPosition = PickPlaylistIndex(playlist);
            wallpaper = _library.Get(playlist.Items[_playlistPosition]);
            var transitionMs = playlist.Transition == "none" ? 0 : playlist.TransitionMs;
            SetPlayback(wallpaper, monitorMap, transitionMs);
            _advanceAt = !playlist.Paused && playlist.IntervalSeconds > 0
                ? DateTimeOffset.UtcNow.AddSeconds(playlist.IntervalSeconds)
                : null;
        }
        else
        {
            wallpaper = profile.WallpaperId is { } wallpaperId ? _library.Get(wallpaperId) : null;
            SetPlayback(wallpaper, monitorMap);
            _advanceAt = null;
        }

        if (!string.Equals(_config.Get("accent_color", "auto"), "none", StringComparison.Ordinal))
        {
            if (!string.IsNullOrWhiteSpace(profile.AccentColor))
                _player.ApplyAccent(profile.AccentColor);
        }
        if (_activePauseReason.HasValue)
            _player.SetPaused(true);
    }

    private void SetPlayback(
        Wallpaper? wallpaper,
        IReadOnlyDictionary<string, Wallpaper?> monitors,
        int transitionMs = 800)
    {
        try
        {
            _player.SetCurrent(wallpaper, monitors, transitionMs);
        }
        catch (Exception exception) when (
            exception is IOException or InvalidOperationException or NotSupportedException or
                System.ComponentModel.Win32Exception)
        {
            var name = wallpaper?.Name ?? "обоев";
            StatusMessage?.Invoke(this, $"Не удалось воспроизвести «{name}»: {exception.Message}");
        }
    }

    private int PickPlaylistIndex(Playlist playlist) =>
        playlist.Shuffle ? _random.Next(playlist.Items.Count) : 0;

    private void EvaluatePolicy(bool force = false)
    {
        var reason = ResolvePauseReason();
        if (reason != _activePauseReason || force)
        {
            var previous = _activePauseReason;
            _activePauseReason = reason;
            _player.SetPaused(reason.HasValue);
            PauseChanged?.Invoke(this, reason.HasValue);
            if (reason.HasValue && !previous.HasValue)
                StatusMessage?.Invoke(this, DescribeReason(reason.Value));
        }

        if (_advanceAt.HasValue && DateTimeOffset.UtcNow >= _advanceAt && !Paused)
            Step(1);
    }

    private PauseReason? ResolvePauseReason()
    {
        if (_manualPause)
            return PauseReason.User;

        var state = SystemPlaybackState.Read();
        if (_config.Get("pause_on_fullscreen", true) && state.Fullscreen)
            return PauseReason.Fullscreen;
        if (_config.Get("pause_on_lock", true) && state.Locked)
            return PauseReason.Locked;
        if (_config.Get("pause_on_battery", false) && state.OnBattery &&
            (_config.Get("battery_pause_threshold", 20) <= 0 ||
             state.BatteryPercent <= _config.Get("battery_pause_threshold", 20)))
            return PauseReason.Battery;
        if (_config.Get("pause_when_idle", false) &&
            state.Idle >= TimeSpan.FromMinutes(_config.Get("idle_minutes", 5)))
            return PauseReason.Suspend;
        if (_config.Get("pause_on_remote_session", true) && IsRemoteSession())
            return PauseReason.RemoteSession;
        return null;
    }

    private void EvaluateRules(bool force = false)
    {
        var now = DateTime.Now;
        var minutes = now.Hour * 60 + now.Minute;
        var weekday = ((int)now.DayOfWeek + 6) % 7;
        var winner = _rules.Where(rule => rule.Matches(minutes, weekday))
            .OrderByDescending(rule => rule.Priority)
            .FirstOrDefault();
        var winnerId = winner?.Id ?? "";
        if (winnerId == _lastRuleId && !force)
            return;
        _lastRuleId = winnerId;

        if (winner is null)
        {
            var startupId = _config.Get("startup_profile_id", "");
            if (!string.IsNullOrEmpty(startupId) && FindProfile(startupId) is not null)
            {
                ActivateProfile(startupId, remember: false);
                StatusMessage?.Invoke(this, "Расписание вернуло базовый профиль");
            }
            return;
        }

        if (FindProfile(winner.ProfileId) is null || ActiveProfile?.Id == winner.ProfileId)
            return;
        ActivateProfile(winner.ProfileId, remember: false);
        var ruleName = winner.Name.Length > 0 ? winner.Name : "правило";
        StatusMessage?.Invoke(this, $"Расписание: {ruleName}");
    }

    private static bool IsRemoteSession() =>
        new[] { "SSH_CONNECTION", "SSH_TTY", "RDP_SESSION", "SESSIONNAME" }
            .Any(name =>
            {
                var value = Environment.GetEnvironmentVariable(name);
                return !string.IsNullOrWhiteSpace(value) &&
                    (name.StartsWith("SSH", StringComparison.Ordinal) ||
                     value.Contains("RDP", StringComparison.OrdinalIgnoreCase));
            });

    private static string DescribeReason(PauseReason reason) => reason switch
    {
        PauseReason.User => "Воспроизведение приостановлено",
        PauseReason.Fullscreen => "Пауза: полноэкранное приложение",
        PauseReason.Battery => "Пауза: питание от батареи",
        PauseReason.Locked => "Пауза: экран заблокирован",
        PauseReason.RemoteSession => "Пауза: удалённый сеанс",
        PauseReason.Suspend => "Пауза: система простаивает",
        _ => "Пауза"
    };

    private void ThrowIfDisposed()
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
    }

    public void Dispose()
    {
        if (_disposed)
            return;
        _disposed = true;
        _policyTimer.Stop();
        _ruleTimer.Stop();
    }
}

public readonly record struct SystemPlaybackSnapshot(
    bool OnBattery,
    int BatteryPercent,
    bool Fullscreen,
    bool Locked,
    TimeSpan Idle)
{
    public static SystemPlaybackSnapshot Read() => SystemPlaybackState.Read();
}

internal static class SystemPlaybackState
{
    public static SystemPlaybackSnapshot Read()
    {
        var power = ReadPower();
        var idle = ReadIdle();
        return new SystemPlaybackSnapshot(power.OnBattery, power.Percent, ReadFullscreen(), ReadLocked(), idle);
    }

    private static (bool OnBattery, int Percent) ReadPower()
    {
        if (OperatingSystem.IsWindows())
        {
            try
            {
                if (GetSystemPowerStatus(out var power))
                {
                    var percent = power.BatteryLifePercent == 255 ? 100 : power.BatteryLifePercent;
                    var onBattery = power.ACLineStatus == 0;
                    return (onBattery, percent);
                }
            }
            catch (DllNotFoundException)
            {
                return (false, 100);
            }
            return (false, 100);
        }

        if (OperatingSystem.IsLinux())
        {
            try
            {
                var root = "/sys/class/power_supply";
                if (!Directory.Exists(root))
                    return (false, 100);
                var batteries = Directory.GetDirectories(root)
                    .Where(path => Path.GetFileName(path).StartsWith("BAT", StringComparison.Ordinal))
                    .Select(path => (Path: path, Capacity: ReadInt(Path.Combine(path, "capacity"))))
                    .Where(item => item.Capacity.HasValue)
                    .ToArray();
                var mainsOnline = Directory.GetDirectories(root)
                    .Where(path => File.Exists(Path.Combine(path, "type")) &&
                        File.ReadAllText(Path.Combine(path, "type")).Trim() == "Mains")
                    .Any(path => ReadInt(Path.Combine(path, "online")) == 1);
                if (batteries.Length == 0)
                    return (!mainsOnline, 100);
                return (!mainsOnline && batteries[0].Capacity < 100, batteries[0].Capacity!.Value);
            }
            catch (IOException)
            {
                return (false, 100);
            }
            catch (UnauthorizedAccessException)
            {
                return (false, 100);
            }
        }

        return (false, 100);
    }

    private static int? ReadInt(string path)
    {
        if (!File.Exists(path) || !int.TryParse(File.ReadAllText(path).Trim(), out var value))
            return null;
        return value;
    }

    private static TimeSpan ReadIdle()
    {
        if (OperatingSystem.IsWindows())
        {
            var lastInput = new LastInputInfo { Size = (uint)Marshal.SizeOf<LastInputInfo>() };
            if (GetLastInputInfo(ref lastInput))
            {
                var tickCount = GetTickCount();
                return TimeSpan.FromMilliseconds((uint)(tickCount - lastInput.Time));
            }
            return TimeSpan.Zero;
        }

        if (!OperatingSystem.IsLinux() || string.IsNullOrEmpty(Environment.GetEnvironmentVariable("DISPLAY")))
            return TimeSpan.Zero;
        try
        {
            using var process = Process.Start(new ProcessStartInfo("xprintidle")
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true
            });
            if (process is null || !process.WaitForExit(1000))
            {
                process?.Kill(entireProcessTree: true);
                return TimeSpan.Zero;
            }
            return long.TryParse(process.StandardOutput.ReadToEnd().Trim(), out var milliseconds)
                ? TimeSpan.FromMilliseconds(Math.Max(0, milliseconds))
                : TimeSpan.Zero;
        }
        catch (System.ComponentModel.Win32Exception)
        {
            return TimeSpan.Zero;
        }
    }

    private static bool ReadFullscreen()
    {
        if (OperatingSystem.IsWindows())
        {
            if (GetForegroundWindow() == GetDesktopWindow())
                return true;
            var foreground = GetForegroundWindow();
            if (foreground == IntPtr.Zero)
                return false;
            if (!GetWindowRect(foreground, out var active) || !GetWindowRect(GetDesktopWindow(), out var desktop))
                return false;
            return active.Right - active.Left >= desktop.Right - desktop.Left - 2 &&
                   active.Bottom - active.Top >= desktop.Bottom - desktop.Top - 2;
        }

        if (!OperatingSystem.IsLinux())
            return false;
        try
        {
            using var process = Process.Start(new ProcessStartInfo("swaymsg", "-t get_tree")
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true
            });
            if (process is null)
                return false;
            var outputTask = process.StandardOutput.ReadToEndAsync();
            if (!process.WaitForExit(1500))
            {
                process.Kill(entireProcessTree: true);
                return false;
            }
            if (process.ExitCode != 0)
                return false;
            using var tree = JsonDocument.Parse(outputTask.GetAwaiter().GetResult());
            return ContainsFocusedFullscreen(tree.RootElement);
        }
        catch (System.ComponentModel.Win32Exception)
        {
            return false;
        }
        catch (JsonException)
        {
            return false;
        }
    }

    private static bool ReadLocked() =>
        OperatingSystem.IsWindows() && GetForegroundWindow() == GetDesktopWindow();

    private static bool ContainsFocusedFullscreen(JsonElement node)
    {
        if (node.ValueKind != JsonValueKind.Object)
            return false;
        if (node.TryGetProperty("is_focused", out var focused) && focused.ValueKind == JsonValueKind.True &&
            node.TryGetProperty("fullscreen_mode", out var fullscreen) &&
            fullscreen.ValueKind == JsonValueKind.Number && fullscreen.GetInt32() == 1)
            return true;

        foreach (var name in new[] { "nodes", "floating_nodes" })
        {
            if (!node.TryGetProperty(name, out var children) || children.ValueKind != JsonValueKind.Array)
                continue;
            foreach (var child in children.EnumerateArray())
                if (ContainsFocusedFullscreen(child))
                    return true;
        }
        return false;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct SystemPowerStatus
    {
        public byte ACLineStatus;
        public byte BatteryFlag;
        public byte BatteryLifePercent;
        public byte Reserved;
        public uint BatteryLifeTime;
        public uint BatteryFullLifeTime;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct LastInputInfo
    {
        public uint Size;
        public uint Time;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct NativeRect
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [DllImport("kernel32.dll", EntryPoint = "GetSystemPowerStatus", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetSystemPowerStatus(out SystemPowerStatus status);

    [DllImport("user32.dll", EntryPoint = "GetLastInputInfo", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetLastInputInfo(ref LastInputInfo info);

    [DllImport("kernel32.dll", EntryPoint = "GetTickCount")]
    private static extern uint GetTickCount();

    [DllImport("user32.dll")]
    private static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll")]
    private static extern IntPtr GetDesktopWindow();

    [DllImport("user32.dll", EntryPoint = "GetWindowRect", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetWindowRect(IntPtr window, out NativeRect rect);
}

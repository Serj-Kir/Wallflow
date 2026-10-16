using System.Text.Json;
using System.Text.Json.Nodes;

namespace Wallflow.App.Core;

public sealed class WallflowConfig
{
    public const int MaximumBackups = 10;

    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        WriteIndented = true
    };

    private readonly JsonObject _data;

    public WallflowConfig()
    {
        WallflowPaths.EnsureDirectories();
        _data = Load();
    }

    public IReadOnlyList<string> LibraryDirectories =>
        _data["library_dirs"] is JsonArray dirs
            ? dirs.Select(item => item?.GetValue<string>()).OfType<string>().ToArray()
            : [];

    public T Get<T>(string key, T fallback)
    {
        var value = _data[key];
        if (value is null)
            return fallback;

        try
        {
            return value.Deserialize<T>(JsonOptions) ?? fallback;
        }
        catch (JsonException)
        {
            return fallback;
        }
    }

    public void Set<T>(string key, T value)
    {
        _data[key] = JsonSerializer.SerializeToNode(value, JsonOptions);
        Save();
    }

    public void ResetDefaults()
    {
        foreach (var key in _data.Select(pair => pair.Key).ToArray())
            _data.Remove(key);
        foreach (var (key, value) in CreateDefaults())
            _data[key] = value?.DeepClone();
        Save();
    }

    public void AddLibraryDirectories(IEnumerable<string> directories)
    {
        var current = LibraryDirectories.ToHashSet(
            OperatingSystem.IsWindows() ? StringComparer.OrdinalIgnoreCase : StringComparer.Ordinal);
        var added = false;
        foreach (var directory in directories)
        {
            var fullPath = Path.GetFullPath(directory);
            if (Directory.Exists(fullPath) && current.Add(fullPath))
            {
                added = true;
            }
        }

        if (!added)
            return;

        _data["library_dirs"] = JsonSerializer.SerializeToNode(current, JsonOptions);
        Save();
    }

    public List<Profile> LoadProfiles() => LoadSection<Profile>("profiles");
    public List<Playlist> LoadPlaylists() => LoadSection<Playlist>("playlists");
    public List<ScheduleRule> LoadRules() => LoadSection<ScheduleRule>("rules");
    public List<MonitorInfo> LoadMonitors() => LoadSection<MonitorInfo>("monitors");

    public void SaveProfiles(IEnumerable<Profile> profiles) => SaveSection("profiles", profiles);
    public void SavePlaylists(IEnumerable<Playlist> playlists) => SaveSection("playlists", playlists);
    public void SaveRules(IEnumerable<ScheduleRule> rules) => SaveSection("rules", rules);
    public void SaveMonitors(IEnumerable<MonitorInfo> monitors) => SaveSection("monitors", monitors);

    private List<T> LoadSection<T>(string name)
    {
        var path = Path.Combine(WallflowPaths.DataDirectory, $"{name}.json");
        if (!File.Exists(path))
            return [];

        return JsonSerializer.Deserialize<List<T>>(File.ReadAllText(path), JsonOptions)
            ?? throw new InvalidDataException($"Configuration section must be a JSON array: {path}");
    }

    private void SaveSection<T>(string name, IEnumerable<T> items)
    {
        var path = Path.Combine(WallflowPaths.DataDirectory, $"{name}.json");
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var temporaryPath = path + ".tmp";
        File.WriteAllText(temporaryPath, JsonSerializer.Serialize(items, JsonOptions) + Environment.NewLine);
        File.Move(temporaryPath, path, overwrite: true);
    }

    private JsonObject Load()
    {
        var path = WallflowPaths.ConfigFile;
        if (!File.Exists(path))
        {
            var defaults = CreateDefaults();
            SaveObject(path, defaults);
            return defaults;
        }

        JsonObject loaded;
        try
        {
            loaded = JsonNode.Parse(File.ReadAllText(path)) as JsonObject
                ?? throw new InvalidDataException($"Configuration root must be a JSON object: {path}");
        }
        catch (Exception exception) when (exception is JsonException or IOException or InvalidDataException)
        {
            loaded = LoadNewestBackup(path) ?? CreateDefaults();
        }

        return MergeDefaults(loaded);
    }

    private void Save()
    {
        WriteBackup(WallflowPaths.ConfigFile);
        SaveObject(WallflowPaths.ConfigFile, _data);
    }

    private static JsonObject MergeDefaults(JsonObject loaded)
    {
        foreach (var (key, value) in CreateDefaults())
            if (!loaded.ContainsKey(key))
                loaded[key] = value?.DeepClone();
        return loaded;
    }

    private static JsonObject CreateDefaults() => new()
    {
        ["version"] = 1,
        ["language"] = "system",
        ["start_with_system"] = false,
        ["minimize_to_tray"] = true,
        ["close_to_tray"] = true,
        ["check_updates"] = true,
        ["static_wallpaper_mode"] = "auto",
        ["linux_wallpaper_fit"] = "fill",
        ["loop_videos"] = true,
        ["video_audio"] = false,
        ["interactive_web_wallpapers"] = true,
        ["pause_on_fullscreen"] = true,
        ["pause_on_lock"] = true,
        ["pause_on_battery"] = false,
        ["battery_pause_threshold"] = 20,
        ["pause_on_remote_session"] = true,
        ["pause_when_idle"] = false,
        ["idle_minutes"] = 5,
        ["memory_limit_mb"] = 1024,
        ["max_fps"] = 30,
        ["hardware_acceleration"] = true,
        ["pause_when_other_app_fullscreen"] = true,
        ["disable_battery_saver_live"] = true,
        ["library_dirs"] = new JsonArray(),
        ["watch_library"] = true,
        ["thumb_size"] = 220,
        ["remember_last_wallpaper"] = true,
        ["accent_color"] = "auto",
        ["hide_desktop_icons"] = false,
        ["screensaver_awareness"] = true,
        ["startup_profile_id"] = "",
        ["last_profile_id"] = "",
        ["last_run_version"] = "",
        ["first_run_done"] = false
    };

    private static JsonObject? LoadNewestBackup(string configPath)
    {
        var directory = Path.Combine(Path.GetDirectoryName(configPath)!, "backups");
        if (!Directory.Exists(directory))
            return null;
        foreach (var backup in Directory.EnumerateFiles(directory, "config-*.json")
                     .OrderByDescending(File.GetLastWriteTimeUtc))
        {
            try
            {
                if (JsonNode.Parse(File.ReadAllText(backup)) is JsonObject recovered)
                    return recovered;
            }
            catch (Exception exception) when (exception is JsonException or IOException or InvalidDataException)
            {
                continue;
            }
        }
        return null;
    }

    private static void WriteBackup(string configPath)
    {
        if (!File.Exists(configPath))
            return;

        var directory = Path.Combine(Path.GetDirectoryName(configPath)!, "backups");
        Directory.CreateDirectory(directory);
        var stamp = DateTime.UtcNow.ToString("yyyyMMdd-HHmmss-fff", System.Globalization.CultureInfo.InvariantCulture);
        File.Copy(configPath, Path.Combine(directory, $"config-{stamp}.json"));
        var oldBackups = Directory.EnumerateFiles(directory, "config-*.json")
            .OrderByDescending(File.GetLastWriteTimeUtc)
            .Skip(MaximumBackups);
        foreach (var oldBackup in oldBackups)
            File.Delete(oldBackup);
    }

    private static void SaveObject(string path, JsonObject data)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var temporaryPath = path + ".tmp";
        File.WriteAllText(temporaryPath, data.ToJsonString(JsonOptions) + Environment.NewLine);
        File.Move(temporaryPath, path, overwrite: true);
    }
}

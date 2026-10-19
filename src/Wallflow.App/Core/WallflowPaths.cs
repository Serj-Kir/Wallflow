namespace Wallflow.App.Core;

public static class WallflowPaths
{
    public const string AppDirectoryName = "wallflow";

    public static string DataDirectory
    {
        get
        {
            var overridePath = Environment.GetEnvironmentVariable("WALLFLOW_DATA_DIR");
            if (!string.IsNullOrWhiteSpace(overridePath))
                return Path.GetFullPath(overridePath);

            if (OperatingSystem.IsWindows())
                return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), AppDirectoryName);
            if (OperatingSystem.IsMacOS())
                return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Library", "Application Support", AppDirectoryName);

            var xdg = Environment.GetEnvironmentVariable("XDG_DATA_HOME");
            var root = string.IsNullOrWhiteSpace(xdg)
                ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".local", "share")
                : xdg;
            return Path.Combine(root, AppDirectoryName);
        }
    }

    public static string WallpaperDirectory => Path.Combine(DataDirectory, "wallpapers");
    public static string ThumbnailDirectory => Path.Combine(DataDirectory, "thumbs");
    public static string LogDirectory => Path.Combine(DataDirectory, "logs");
    public static string CacheDirectory => Path.Combine(CacheRoot, AppDirectoryName);

    private static string CacheRoot
    {
        get
        {
            if (OperatingSystem.IsWindows())
                return Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            if (OperatingSystem.IsMacOS())
                return Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Library", "Caches");

            return Environment.GetEnvironmentVariable("XDG_CACHE_HOME") is { Length: > 0 } cache
                ? cache
                : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".cache");
        }
    }
    public static string ConfigFile => Path.Combine(DataDirectory, "config.json");
    public static string LibraryFile => Path.Combine(DataDirectory, "library.json");

    public static void EnsureDirectories()
    {
        foreach (var name in new[] { "wallpapers", "covers", "thumbs", "logs", "backups" })
            Directory.CreateDirectory(Path.Combine(DataDirectory, name));
        Directory.CreateDirectory(CacheDirectory);
    }
}

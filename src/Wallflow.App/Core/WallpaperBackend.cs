using System.Diagnostics;
using System.ComponentModel;
using System.Runtime.InteropServices;

namespace Wallflow.App.Core;

public interface IWallpaperBackend
{
    string Name { get; }
    bool SetWallpaper(string path, string fit = "fill");
}

public static class WallpaperBackendFactory
{
    public static IWallpaperBackend Create()
    {
        if (OperatingSystem.IsWindows())
            return new WindowsWallpaperBackend();
        if (OperatingSystem.IsMacOS())
            return new MacOsWallpaperBackend();
        if (OperatingSystem.IsLinux())
            return new LinuxWallpaperBackend();

        throw new PlatformNotSupportedException("Wallflow does not support this operating system.");
    }
}

internal sealed class WindowsWallpaperBackend : IWallpaperBackend
{
    private const uint SpiSetDesktopWallpaper = 0x0014;
    private const uint UpdateIniFile = 0x01;
    private const uint SendChange = 0x02;

    public string Name => "Windows";

    public bool SetWallpaper(string path, string fit = "fill")
    {
        if (!File.Exists(path))
            throw new FileNotFoundException("Wallpaper image does not exist.", path);
        return SystemParametersInfo(SpiSetDesktopWallpaper, 0, Path.GetFullPath(path), UpdateIniFile | SendChange);
    }

    [DllImport("user32.dll", EntryPoint = "SystemParametersInfoW", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SystemParametersInfo(uint action, uint parameter, string value, uint flags);
}

internal sealed class MacOsWallpaperBackend : IWallpaperBackend
{
    public string Name => "macOS";

    public bool SetWallpaper(string path, string fit = "fill")
    {
        if (!File.Exists(path))
            throw new FileNotFoundException("Wallpaper image does not exist.", path);

        var escapedPath = Path.GetFullPath(path).Replace("\\", "\\\\").Replace("\"", "\\\"");
        return Run("/usr/bin/osascript", "-e",
            $"tell application \"System Events\" to set picture of every desktop to POSIX file \"{escapedPath}\"");
    }

    private static bool Run(string executable, params string[] arguments)
    {
        if (!File.Exists(executable))
            return false;
        try
        {
            using var process = Process.Start(CreateStartInfo(executable, arguments));
            if (process is null)
                return false;
            if (!process.WaitForExit(5000))
            {
                process.Kill(entireProcessTree: true);
                return false;
            }
            return process.ExitCode == 0;
        }
        catch (Win32Exception)
        {
            return false;
        }
    }

    private static ProcessStartInfo CreateStartInfo(string executable, IEnumerable<string> arguments)
    {
        var info = new ProcessStartInfo(executable) { UseShellExecute = false, CreateNoWindow = true };
        foreach (var argument in arguments)
            info.ArgumentList.Add(argument);
        return info;
    }
}

internal sealed class LinuxWallpaperBackend : IWallpaperBackend
{
    private const string GnomeSchema = "org.gnome.desktop.background";
    private const string CinnamonSchema = "org.cinnamon.desktop.background";
    private readonly Func<string, IReadOnlyList<string>, bool> _runCommand;
    private readonly string _desktop;
    private readonly bool _isWayland;

    public LinuxWallpaperBackend(
        Func<string, IReadOnlyList<string>, bool>? runCommand = null,
        string? desktop = null,
        bool? isWayland = null)
    {
        _runCommand = runCommand ?? ((executable, arguments) => Run(executable, arguments.ToArray()));
        _desktop = (desktop ?? Environment.GetEnvironmentVariable("XDG_CURRENT_DESKTOP") ?? "")
            .ToLowerInvariant();
        _isWayland = isWayland ??
            (string.Equals(Environment.GetEnvironmentVariable("XDG_SESSION_TYPE"), "wayland", StringComparison.OrdinalIgnoreCase) ||
             !string.IsNullOrEmpty(Environment.GetEnvironmentVariable("WAYLAND_DISPLAY")));
    }

    public string Name => "Linux";

    public bool SetWallpaper(string path, string fit = "fill")
    {
        if (!File.Exists(path))
            throw new FileNotFoundException("Wallpaper image does not exist.", path);

        var fullPath = Path.GetFullPath(path);
        var uri = new Uri(fullPath).AbsoluteUri;
        if (IsGnomeOrCinnamon())
        {
            if (SetGnomeWallpaper(uri, fit))
                return true;
            if (_isWayland)
                return false;
        }

        if (!IsGnomeOrCinnamon() &&
            _runCommand("gsettings", ["set", GnomeSchema, "picture-uri", uri]))
            return true;

        if (_desktop.Contains("xfce") && SetXfceWallpaper(fullPath))
            return true;

        if (_desktop.Contains("kde") && _runCommand("plasma-apply-wallpaperimage", [fullPath]))
            return true;

        if (_desktop.Contains("sway") && _runCommand("swaymsg", ["output", "*", "bg", fullPath, "fill"]))
            return true;

        if (_desktop.Contains("hyprland") &&
            (_runCommand("hyprctl", ["hyprpaper", "wallpaper", $",{fullPath}"]) ||
             _runCommand("hyprctl", ["dispatch", "exec", $"hyprpaper -- {fullPath}"])))
            return true;

        return !_isWayland && _runCommand("feh", ["--bg-fill", fullPath]);
    }

    private static bool SetXfceWallpaper(string path)
    {
        var query = RunForOutput("xfconf-query", ["-c", "xfce4-desktop", "-l"]);
        if (query is null)
            return false;

        var properties = query.Split(Environment.NewLine, StringSplitOptions.RemoveEmptyEntries)
            .Where(property => property.Contains("/backdrop/", StringComparison.Ordinal) &&
                               property.EndsWith("/last-image", StringComparison.Ordinal))
            .ToArray();
        if (properties.Length == 0)
            return Run("xfconf-query", "-c", "xfce4-desktop", "-p",
                "/backdrop/screen0/monitor0/workspace0/last-image", "--create", "-t", "string", "-s", path);

        return properties.All(property =>
            Run("xfconf-query", "-c", "xfce4-desktop", "-p", property, "-s", path));
    }

    private bool SetGnomeWallpaper(string uri, string fit)
    {
        var schema = _desktop.Contains("cinnamon") ? CinnamonSchema : GnomeSchema;
        var settings = new List<(string Key, string Value)>
        {
            ("picture-uri", uri),
            ("picture-options", GnomePictureOptionForFit(fit))
        };

        var gsettingsSucceeded = true;
        foreach (var setting in settings)
            gsettingsSucceeded &= _runCommand("gsettings", ["set", schema, setting.Key, setting.Value]);
        if (schema == GnomeSchema)
            _runCommand("gsettings", ["set", schema, "picture-uri-dark", uri]);
        if (gsettingsSucceeded)
            return true;

        var basePath = schema == CinnamonSchema
            ? "/org/cinnamon/desktop/background/"
            : "/org/gnome/desktop/background/";
        var dconfSucceeded = true;
        foreach (var (key, value) in settings)
        {
            var serializedValue = System.Text.Json.JsonSerializer.Serialize(value);
            dconfSucceeded &= _runCommand("dconf", ["write", basePath + key, serializedValue]);
        }
        if (schema == GnomeSchema)
        {
            var serializedUri = System.Text.Json.JsonSerializer.Serialize(uri);
            _runCommand("dconf", ["write", basePath + "picture-uri-dark", serializedUri]);
        }
        return dconfSucceeded;
    }

    internal static string GnomePictureOptionForFit(string fit) => fit.ToLowerInvariant() switch
    {
        "fit" => "scaled",
        "stretch" => "stretched",
        "center" => "centered",
        "tile" => "wallpaper",
        _ => "zoom"
    };

    private bool IsGnomeOrCinnamon() =>
        _desktop.Contains("gnome") || _desktop.Contains("unity") || _desktop.Contains("cinnamon") ||
        _desktop.Contains("mate") || _desktop.Contains("budgie");

    private static bool Run(string executable, params string[] arguments)
    {
        try
        {
            using var process = Process.Start(CreateStartInfo(executable, arguments));
            if (process is null)
                return false;
            if (!process.WaitForExit(5000))
            {
                process.Kill(entireProcessTree: true);
                return false;
            }

            return process.ExitCode == 0;
        }
        catch (Win32Exception)
        {
            return false;
        }
    }

    private static string? RunForOutput(string executable, params string[] arguments)
    {
        try
        {
            using var process = Process.Start(CreateStartInfo(executable, arguments, redirectOutput: true));
            if (process is null)
                return null;

            var outputTask = process.StandardOutput.ReadToEndAsync();
            if (!process.WaitForExit(5000))
            {
                process.Kill(entireProcessTree: true);
                return null;
            }
            var output = outputTask.GetAwaiter().GetResult();
            return process.ExitCode == 0 ? output : null;
        }
        catch (Win32Exception)
        {
            return null;
        }
    }

    private static ProcessStartInfo CreateStartInfo(
        string executable,
        IEnumerable<string> arguments,
        bool redirectOutput = false)
    {
        var info = new ProcessStartInfo(executable)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = redirectOutput,
            RedirectStandardError = redirectOutput
        };
        foreach (var argument in arguments)
            info.ArgumentList.Add(argument);
        return info;
    }
}

using System.Diagnostics;
using System.ComponentModel;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Security;
using Microsoft.Win32;

namespace Wallflow.App.Core;

public interface IWallpaperBackend
{
    string Name { get; }
    bool SetWallpaper(string path, string fit = "fill");
    bool SetAccentColor(string hexColor);
}

/// <summary>
/// Shared helpers for the platform backends.
/// </summary>
internal static class WallpaperBackendHelpers
{
    /// <summary>
    /// Normalises ``#rgb`` / ``#rrggbb`` / ``#rrggbbaa`` into ``#rrggbb``.
    /// Unknown input becomes ``#000000``.
    /// </summary>
    public static string NormalizeHex(string? color)
    {
        var value = color?.Trim().TrimStart('#') ?? string.Empty;
        if (value.Length == 3)
            value = string.Concat(value.Select(character => character.ToString() + character));
        if (value.Length == 8)
            value = value[..6];
        if (value.Length != 6 || !value.All(char.IsAsciiHexDigit))
            return "#000000";
        return "#" + value.ToLowerInvariant();
    }
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
        ApplyStyle(fit);
        return SystemParametersInfo(SpiSetDesktopWallpaper, 0, Path.GetFullPath(path), UpdateIniFile | SendChange);
    }

    private void ApplyStyle(string fit)
    {
#pragma warning disable CA1416 // Registry is only available on Windows
        var style = fit.ToLowerInvariant() switch
        {
            "fit" => "Center",
            "stretch" => "Stretch",
            "center" => "Center",
            "tile" => "Tile",
            _ => "Fill"
        };
        try
        {
            using var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(
                @"Control Panel\Desktop", writable: true);
            if (key is null)
                return;
            key.SetValue("WallpaperStyle", style, Microsoft.Win32.RegistryValueKind.String);
            key.SetValue("TileWallpaper", style == "Tile" ? "1" : "0",
                Microsoft.Win32.RegistryValueKind.String);
        }
        catch (Exception exception) when (
            exception is IOException or SecurityException or InvalidOperationException)
        {
        }
#pragma warning restore CA1416
    }

    public bool SetAccentColor(string hexColor)
    {
#pragma warning disable CA1416 // Registry is only available on Windows
        var normalized = WallpaperBackendHelpers.NormalizeHex(hexColor);
        if (normalized.Length != 7 ||
            !byte.TryParse(normalized[1..3], NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var red) ||
            !byte.TryParse(normalized[3..5], NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var green) ||
            !byte.TryParse(normalized[5..7], NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var blue))
            return false;

        // The same value the Windows shell uses for its own colourisation.
        var value = (0x99 << 24) | (blue << 16) | (green << 8) | red;
        try
        {
            using var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(
                @"Software\Microsoft\Windows\DWM", writable: true);
            if (key is null)
                return false;
            key.SetValue("ColorizationColor", value, Microsoft.Win32.RegistryValueKind.DWord);
        }
        catch (Exception exception) when (
            exception is IOException or SecurityException or InvalidOperationException)
        {
        }
#pragma warning restore CA1416
        return true;
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

    public bool SetAccentColor(string hexColor) => false;

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

    public bool SetAccentColor(string hexColor)
    {
        var color = WallpaperBackendHelpers.NormalizeHex(hexColor);
        if (_desktop.Contains("sway"))
        {
            return _runCommand("swaymsg", ["output", "*", "bg", color, "solid_color"]);
        }

        if (_desktop.Contains("kde"))
        {
            // Match the desktop colour used by KDE's own colourisation.
            var script =
                "var allDesktops = desktops();\n" +
                "for (var i = 0; i < allDesktops.length; i++) {\n" +
                $"    allDesktops[i].color = \"{color}\";\n" +
                "}";
            return _runCommand("qdbus6", ["org.kde.plasmashell", "/PlasmaShell",
                           "org.kde.PlasmaShell.evaluateScript", script]) ||
                   _runCommand("qdbus", ["org.kde.plasmashell", "/PlasmaShell",
                       "org.kde.PlasmaShell.evaluateScript", script]);
        }

        return false;
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

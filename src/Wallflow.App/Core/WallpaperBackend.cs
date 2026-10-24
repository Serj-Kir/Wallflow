using System.Diagnostics;
using System.ComponentModel;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Security;
using Microsoft.Win32;

namespace Wallflow.App.Core;

/// <summary>What a desktop backend can do without application windows.</summary>
public readonly record struct BackendCapabilities(bool PerMonitor, bool AccentColor, bool ClearToColor);

public interface IWallpaperBackend
{
    string Name { get; }
    BackendCapabilities Capabilities { get; }
    bool SetWallpaper(string path, string fit = "fill");
    bool Clear();
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

    public static ProcessStartInfo StartInfo(string executable, IEnumerable<string> arguments, bool redirectOutput = false) =>
        StartInfoWith(executable, arguments, redirectOutput);

    public static bool Run(string executable, params string[] arguments) =>
        RunWith(executable, arguments);

    public static string? RunForOutput(string executable, params string[] arguments) =>
        RunForOutputWith(executable, arguments);

    private static ProcessStartInfo StartInfoWith(string executable, IEnumerable<string> arguments, bool redirectOutput)
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

    private static bool RunWith(string executable, IEnumerable<string> arguments)
    {
        try
        {
            using var process = Process.Start(StartInfoWith(executable, arguments, redirectOutput: false));
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

    private static string? RunForOutputWith(string executable, IEnumerable<string> arguments)
    {
        try
        {
            using var process = Process.Start(StartInfoWith(executable, arguments, redirectOutput: true));
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
    public BackendCapabilities Capabilities => new(PerMonitor: true, AccentColor: true, ClearToColor: true);

    public bool SetWallpaper(string path, string fit = "fill")
    {
        if (!File.Exists(path))
            throw new FileNotFoundException("Wallpaper image does not exist.", path);
        ApplyStyle(fit);
        var fullPath = PrepareImage(path);
        if (SystemParametersInfo(SpiSetDesktopWallpaper, 0, fullPath, UpdateIniFile | SendChange))
            return true;
        Log.Warn($"Windows rejected wallpaper '{fullPath}': {new Win32Exception(Marshal.GetLastWin32Error()).Message}");
        return false;
    }

    internal static string PrepareImage(string path)
    {
        var fullPath = Path.GetFullPath(path);
        var extension = Path.GetExtension(fullPath).ToLowerInvariant();
        if (extension is ".bmp" or ".jpg" or ".jpeg" or ".png")
            return fullPath;

        // SPI does not support all the formats the library can import (notably WebP).
        using var bitmap = SkiaSharp.SKBitmap.Decode(fullPath)
            ?? throw new InvalidDataException($"Could not decode wallpaper image: {fullPath}");
        using var image = SkiaSharp.SKImage.FromBitmap(bitmap);
        using var data = image.Encode(SkiaSharp.SKEncodedImageFormat.Png, 100);
        var directory = Path.Combine(WallflowPaths.CacheDirectory, "desktop");
        Directory.CreateDirectory(directory);
        var destination = Path.Combine(directory, Wallpaper.IdForPath(fullPath) + ".png");
        var temporary = destination + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            using (var stream = File.Create(temporary))
                data.SaveTo(stream);
            File.Move(temporary, destination, overwrite: true);
        }
        finally
        {
            if (File.Exists(temporary))
                File.Delete(temporary);
        }
        return destination;
    }

    public bool Clear() => SystemParametersInfo(SpiSetDesktopWallpaper, 0, "", UpdateIniFile | SendChange);

    private static void ApplyStyle(string fit)
    {
#pragma warning disable CA1416 // Registry is only available on Windows
        // WallpaperStyle is the documented (0 = centre, 2 = stretch, 6 = fit, 10 = fill, 22 = span).
        var (style, tile) = fit.ToLowerInvariant() switch
        {
            "fit" => ("6", "0"),
            "stretch" => ("2", "0"),
            "center" => ("0", "0"),
            "tile" => ("0", "1"),
            _ => ("10", "0")
        };
        try
        {
            using var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(
                @"Control Panel\Desktop", writable: true);
            if (key is null)
                return;
            key.SetValue("WallpaperStyle", style, Microsoft.Win32.RegistryValueKind.String);
            key.SetValue("TileWallpaper", tile, Microsoft.Win32.RegistryValueKind.String);
        }
        catch (Exception exception) when (
            exception is IOException or UnauthorizedAccessException or SecurityException or InvalidOperationException)
        {
            Log.Warn("Could not persist the Windows wallpaper fit mode", exception);
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
            exception is IOException or UnauthorizedAccessException or SecurityException or InvalidOperationException)
        {
            Log.Warn("Could not set the Windows accent colour", exception);
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
    public BackendCapabilities Capabilities => new(PerMonitor: false, AccentColor: false, ClearToColor: false);

    public bool SetWallpaper(string path, string fit = "fill")
    {
        if (!File.Exists(path))
            throw new FileNotFoundException("Wallpaper image does not exist.", path);

        var escapedPath = Path.GetFullPath(path).Replace("\\", "\\\\").Replace("\"", "\\\"");
        return WallpaperBackendHelpers.Run("/usr/bin/osascript", "-e",
            $"tell application \"System Events\" to set picture of every desktop to POSIX file \"{escapedPath}\"");
    }

    public bool Clear() => false;

    public bool SetAccentColor(string hexColor) => false;
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
        _runCommand = runCommand ?? ((executable, arguments) => WallpaperBackendHelpers.Run(executable, arguments.ToArray()));
        _desktop = (desktop ?? Environment.GetEnvironmentVariable("XDG_CURRENT_DESKTOP") ?? "")
            .ToLowerInvariant();
        _isWayland = isWayland ?? DesktopEnvironment.Detect().IsWayland;
    }

    public string Name => "Linux";
    public BackendCapabilities Capabilities => new(
        PerMonitor: _desktop.Contains("kde") || _desktop.Contains("plasma") || _desktop.Contains("xfce") ||
                    _desktop.Contains("sway") || _desktop.Contains("hyprland") || _desktop.Contains("lxqt"),
        AccentColor: _desktop.Contains("sway") || _desktop.Contains("kde"),
        ClearToColor: !IsGnomeOrCinnamon());

    public bool SetWallpaper(string path, string fit = "fill")
    {
        if (!File.Exists(path))
            throw new FileNotFoundException("Wallpaper image does not exist.", path);

        var fullPath = Path.GetFullPath(path);
        var uri = new Uri(fullPath).AbsoluteUri;
        var normalizedFit = NormalizeFit(fit);
        if (IsGnomeOrCinnamon())
        {
            if (SetGnomeWallpaper(uri, normalizedFit))
                return true;
            if (_isWayland)
                return false;
        }

        if (!IsGnomeOrCinnamon() &&
            _runCommand("gsettings", ["set", GnomeSchema, "picture-uri", uri]))
            return true;

        if (_desktop.Contains("xfce") && SetXfceWallpaper(fullPath, normalizedFit))
            return true;

        if (_desktop.Contains("kde") && _runCommand("plasma-apply-wallpaperimage", [fullPath]))
            return true;

        if (_desktop.Contains("sway") &&
            _runCommand("swaymsg", ["output", "*", "bg", fullPath, SwayFit(normalizedFit)]))
            return true;

        if (_desktop.Contains("hyprland") && SetHyprlandWallpaper(fullPath))
            return true;

        if (_desktop.Contains("lxqt") && SetLxqtWallpaper(fullPath))
            return true;

        if (!_isWayland && _runCommand("feh", [$"--bg-{FehStyle(normalizedFit)}", fullPath]))
            return true;

        // Last resort on X11: only stretching is possible without a helper tool.
        return !_isWayland && normalizedFit == "stretch" &&
            _runCommand("xsetroot", ["-bitmap", fullPath]);
    }

    public bool Clear()
    {
        if (IsGnomeOrCinnamon())
            return SetGnomeWallpaper("", "fill");

        if (_desktop.Contains("sway"))
            return _runCommand("swaymsg", ["output", "*", "bg", "#000000", "solid_color"]);
        if (_desktop.Contains("kde"))
            return SetAccentColor("#000000");
        if (_desktop.Contains("lxqt"))
            return SetLxqtWallpaper("");
        return !_isWayland && _runCommand("xsetroot", ["-solid", "#000000"]);
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

    private bool SetXfceWallpaper(string path, string fit)
    {
        var style = fit switch
        {
            "fit" => "5",
            "stretch" => "3",
            "center" => "1",
            "tile" => "2",
            _ => "4"
        };
        var query = WallpaperBackendHelpers.RunForOutput("xfconf-query", "-c", "xfce4-desktop", "-l");
        if (query is null)
            return false;

        var properties = query.Split(Environment.NewLine, StringSplitOptions.RemoveEmptyEntries)
            .Where(property => property.Contains("/backdrop/", StringComparison.Ordinal) &&
                               property.EndsWith("/last-image", StringComparison.Ordinal))
            .ToArray();
        if (properties.Length == 0)
        {
            return WallpaperBackendHelpers.Run("xfconf-query", "-c", "xfce4-desktop", "-p",
                       "/backdrop/screen0/monitor0/workspace0/last-image", "--create", "-t", "string", "-s", path) &&
                   WallpaperBackendHelpers.Run("xfconf-query", "-c", "xfce4-desktop", "-p",
                       "/backdrop/screen0/monitor0/workspace0/image-style", "--create", "-t", "int", "-s", style);
        }

        return properties.All(property =>
            WallpaperBackendHelpers.Run("xfconf-query", "-c", "xfce4-desktop", "-p", property, "-s", path) &&
            WallpaperBackendHelpers.Run("xfconf-query", "-c", "xfce4-desktop", "-p",
                property[..^"/last-image".Length] + "/image-style", "-s", style));
    }

    private bool SetHyprlandWallpaper(string path)
    {
        if (_runCommand("hyprctl", ["hyprpaper", "wallpaper", $",{path}"]))
            return true;
        return _runCommand("hyprctl", ["dispatch", "exec", $"hyprpaper -- {path}"]) ||
               _runCommand("hyprctl", ["wallpaper", "set", path]);
    }

    private bool SetLxqtWallpaper(string path)
    {
        var configuration = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            ".config", "pcmanfm-qt", "wallpaper");
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(configuration)!);
            if (path.Length == 0)
            {
                File.WriteAllText(configuration, "");
            }
            else
            {
                var uri = new Uri(Path.GetFullPath(path)).AbsoluteUri;
                File.WriteAllText(configuration, $"Screen-1/{uri}" + Environment.NewLine);
            }
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
            Log.Warn("Could not write the LXQt wallpaper configuration", exception);
            return false;
        }

        return _runCommand("pcmanfm-qt", ["--reconfigure"]);
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

    internal static string SwayFit(string fit) => fit.ToLowerInvariant() switch
    {
        "fit" => "fit",
        "stretch" => "stretch",
        "center" => "center",
        _ => "fill"
    };

    internal static string FehStyle(string fit) => fit.ToLowerInvariant() switch
    {
        "fit" => "fit",
        "stretch" => "scale",
        "center" => "center",
        "tile" => "tile",
        _ => "fill"
    };

    internal static string NormalizeFit(string fit)
    {
        var normalized = fit.ToLowerInvariant();
        return normalized is "fill" or "fit" or "stretch" or "center" or "tile" ? normalized : "fill";
    }

    private bool IsGnomeOrCinnamon() =>
        _desktop.Contains("gnome") || _desktop.Contains("unity") || _desktop.Contains("cinnamon") ||
        _desktop.Contains("mate") || _desktop.Contains("budgie");
}

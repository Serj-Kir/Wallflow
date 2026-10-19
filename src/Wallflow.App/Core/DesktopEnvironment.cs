namespace Wallflow.App.Core;

/// <summary>
/// The desktop environment and display session the application is running in,
/// plus what the active platform backend can do without application windows.
/// </summary>
public sealed record DesktopSession(string Desktop, string SessionType, bool IsWayland)
{
    public string Label => Desktop switch
    {
        "kde" or "plasma" => "KDE Plasma",
        "gnome" => "GNOME",
        "cinnamon" => "Cinnamon",
        "mate" => "MATE",
        "unity" => "Unity",
        "budgie" => "Budgie",
        "xfce" => "XFCE",
        "sway" => "Sway",
        "hyprland" => "Hyprland",
        "lxqt" => "LXQt",
        "windows" => "Windows",
        "macos" => "macOS",
        _ => "Linux"
    };
}

public static class DesktopEnvironment
{
    private static readonly (string Token, string Name)[] KnownDesktops =
    [
        ("kde", "kde"),
        ("plasma", "plasma"),
        ("gnome", "gnome"),
        ("sway", "sway"),
        ("hyprland", "hyprland"),
        ("xfce", "xfce"),
        ("cinnamon", "cinnamon"),
        ("mate", "mate"),
        ("lxqt", "lxqt"),
        ("budgie", "budgie")
    ];

    public static DesktopSession Detect()
    {
        if (OperatingSystem.IsWindows())
            return new DesktopSession("windows", "x11", false);
        if (OperatingSystem.IsMacOS())
            return new DesktopSession("macos", "x11", false);

        var raw = Environment.GetEnvironmentVariable("XDG_CURRENT_DESKTOP") ?? "";
        var separator = raw.IndexOfAny([':', ';']);
        var first = (separator >= 0 ? raw[..separator] : raw).Trim().ToLowerInvariant();
        foreach (var (token, name) in KnownDesktops)
        {
            if (first.Contains(token, StringComparison.Ordinal) ||
                raw.Contains(token, StringComparison.OrdinalIgnoreCase))
                return new DesktopSession(name, SessionType(), IsWaylandSession());
        }

        if (!string.IsNullOrEmpty(Environment.GetEnvironmentVariable("SWAYSOCK")))
            return new DesktopSession("sway", SessionType(), IsWaylandSession());
        if (!string.IsNullOrEmpty(Environment.GetEnvironmentVariable("HYPRLAND_INSTANCE_SIGNATURE")))
            return new DesktopSession("hyprland", SessionType(), IsWaylandSession());

        return new DesktopSession("unknown", SessionType(), IsWaylandSession());
    }

    public static string SessionType()
    {
        if (OperatingSystem.IsWindows() || OperatingSystem.IsMacOS())
            return "x11";
        return IsWaylandSession() ? "wayland" : string.IsNullOrEmpty(Environment.GetEnvironmentVariable("DISPLAY"))
            ? "headless"
            : "x11";
    }

    private static bool IsWaylandSession() =>
        string.Equals(Environment.GetEnvironmentVariable("XDG_SESSION_TYPE"), "wayland",
            StringComparison.OrdinalIgnoreCase) ||
        !string.IsNullOrEmpty(Environment.GetEnvironmentVariable("WAYLAND_DISPLAY"));
}

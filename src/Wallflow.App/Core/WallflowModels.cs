using System.Text.Json.Serialization;

namespace Wallflow.App.Core;

public enum TransitionKind
{
    None,
    Fade,
    Slide,
    Zoom,
    Wipe
}

public enum PauseReason
{
    User,
    Fullscreen,
    Battery,
    Locked,
    RemoteSession,
    Suspend,
    Playback
}

public sealed class MonitorAssignment
{
    public required string MonitorId { get; set; }
    public string? WallpaperId { get; set; }
    public string Fit { get; set; } = "fill";
    public string Alignment { get; set; } = "center";
}

public sealed class Profile
{
    public string Id { get; set; } = NewId();
    public string Name { get; set; } = "Profile";
    public string? WallpaperId { get; set; }
    public string? PlaylistId { get; set; }
    public List<MonitorAssignment> Monitors { get; set; } = [];
    public string AccentColor { get; set; } = "";
    public bool Locked { get; set; }

    public void SetMonitor(
        string monitorId,
        string? wallpaperId,
        string? fit = null,
        string? alignment = null)
    {
        var assignment = Monitors.FirstOrDefault(item => item.MonitorId == monitorId);
        if (assignment is null)
            Monitors.Add(new MonitorAssignment
            {
                MonitorId = monitorId,
                WallpaperId = wallpaperId,
                Fit = fit ?? "fill",
                Alignment = alignment ?? "center"
            });
        else
        {
            assignment.WallpaperId = wallpaperId;
            if (fit is not null)
                assignment.Fit = fit;
            if (alignment is not null)
                assignment.Alignment = alignment;
        }
    }

    private static string NewId() => Guid.NewGuid().ToString("N")[..12];
}

public sealed class Playlist
{
    public string Id { get; set; } = Guid.NewGuid().ToString("N")[..12];
    public string Name { get; set; } = "Playlist";
    public List<string> Items { get; set; } = [];
    public bool Shuffle { get; set; }
    public int IntervalSeconds { get; set; }
    public string Transition { get; set; } = "fade";
    public int TransitionMs { get; set; } = 800;
    public bool Paused { get; set; }
}

public sealed class ScheduleRule
{
    private static readonly string[] WeekdayLabels = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

    public string Id { get; set; } = Guid.NewGuid().ToString("N")[..12];
    public string Name { get; set; } = "";
    public bool Enabled { get; set; } = true;
    public List<int> Weekdays { get; set; } = [];
    public int StartMinutes { get; set; }
    public int EndMinutes { get; set; } = 24 * 60;
    public string ProfileId { get; set; } = "";
    public int Priority { get; set; }
    [JsonIgnore]
    public string Summary
    {
        get
        {
            var days = Weekdays.Count == 0
                ? "каждый день"
                : string.Join(",", Weekdays.Where(day => day >= 0 && day < WeekdayLabels.Length)
                    .Distinct().Order().Select(day => WeekdayLabels[day]));
            var start = $"{StartMinutes / 60:D2}:{StartMinutes % 60:D2}";
            var end = $"{EndMinutes / 60:D2}:{EndMinutes % 60:D2}";
            return $"{(Enabled ? "" : "Выключено · ")}{days} · {start}–{end} · приоритет {Priority}";
        }
    }

    public bool Matches(int minutesOfDay, int weekday)
    {
        if (!Enabled || Weekdays.Count > 0 && !Weekdays.Contains(weekday))
            return false;
        if (StartMinutes == EndMinutes)
            return true;
        return StartMinutes < EndMinutes
            ? minutesOfDay >= StartMinutes && minutesOfDay < EndMinutes
            : minutesOfDay >= StartMinutes || minutesOfDay < EndMinutes;
    }
}

public sealed class MonitorInfo
{
    public required string Id { get; set; }
    public string Name { get; set; } = "";
    public int Width { get; set; }
    public int Height { get; set; }
    public int X { get; set; }
    public int Y { get; set; }
    public double Scale { get; set; } = 1;
    public bool Primary { get; set; }
    public string Identifier { get; set; } = "";
}

public static class WallflowIds
{
    public static string NewId() => Guid.NewGuid().ToString("N")[..12];
}

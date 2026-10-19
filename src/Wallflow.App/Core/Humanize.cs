namespace Wallflow.App.Core;

/// <summary>Human-readable sizes and durations for the UI.</summary>
public static class Humanize
{
    public static string Bytes(long bytes)
    {
        string[] units = ["Б", "КБ", "МБ", "ГБ", "ТБ"];
        var value = (double)Math.Max(0, bytes);
        var unit = 0;
        while (value >= 1024 && unit < units.Length - 1)
        {
            value /= 1024;
            unit++;
        }
        return unit == 0
            ? string.Create(System.Globalization.CultureInfo.InvariantCulture, $"{(long)value} {units[unit]}")
            : string.Create(System.Globalization.CultureInfo.InvariantCulture, $"{value:0.#} {units[unit]}");
    }

    public static string Duration(TimeSpan duration)
    {
        if (duration.TotalSeconds < 60)
            return $"{Math.Max(0, (int)duration.TotalSeconds)} с";
        if (duration.TotalMinutes < 60)
            return $"{(int)duration.TotalMinutes} мин {duration.Seconds} с";
        if (duration.TotalHours < 24)
            return $"{(int)duration.TotalHours} ч {duration.Minutes} мин";
        return $"{(int)duration.TotalDays} д {duration.Hours} ч";
    }
}

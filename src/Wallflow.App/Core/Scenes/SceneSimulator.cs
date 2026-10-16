namespace Wallflow.App.Core.Scenes;

public sealed class SceneSimulator
{
    private readonly Random _random = new(1337);
    private List<double[]> _particles = [];
    private bool _seeded;
    private double _connectDistance;

    public SceneSimulator(SceneSpec spec, int width = 1920, int height = 1080)
    {
        Spec = spec;
        Width = Math.Max(1, width);
        Height = Math.Max(1, height);
    }

    public SceneSpec Spec { get; }
    public int Width { get; private set; }
    public int Height { get; private set; }
    public double Time { get; private set; }
    public IReadOnlyList<double[]> Sample()
    {
        SeedIfNeeded();
        return _particles;
    }
    public double ConnectDistance => _connectDistance;

    public void Resize(int width, int height)
    {
        width = Math.Max(1, width);
        height = Math.Max(1, height);
        if (width == Width && height == Height)
            return;
        Width = width;
        Height = height;
        _seeded = false;
    }

    public void ResetTime() => Time = 0;

    public void Step(double dt)
    {
        dt = Math.Clamp(dt, 0, 0.1);
        Time += dt;
        SeedIfNeeded();
        switch (Spec.Kind)
        {
            case "particles": StepParticles(dt); break;
            case "rain": StepRain(dt); break;
            case "bubbles": StepBubbles(dt); break;
            case "starfield": StepStarfield(dt); break;
        }
    }

    public IReadOnlyList<RgbColor> Palette()
    {
        var colors = Spec.Background.Colors.Count > 0
            ? Spec.Background.Colors
            : string.IsNullOrEmpty(Spec.Background.Color) ? [] : [Spec.Background.Color];
        var parsed = colors.Take(8).Select(SceneColor.Parse).Where(color => color.HasValue)
            .Select(color => color!.Value).ToList();
        return parsed.Count > 0 ? parsed : [new RgbColor(16, 16, 24)];
    }

    private void SeedIfNeeded()
    {
        if (_seeded)
            return;
        var count = CountFor(Spec.Kind);
        var rows = new List<double[]>(count);
        for (var i = 0; i < count; i++)
        {
            if (Spec.Kind == "rain")
                rows.Add([Next(0, Width), Next(-Height, Height), Next(6, 18)]);
            else if (Spec.Kind == "bubbles")
                rows.Add([Next(0, Width), Next(0, Height), Next(4, 26), Next(0.6, 1.9), Next(0.15, 0.6)]);
            else if (Spec.Kind == "starfield")
                rows.Add([Next(0, Width), Next(0, Height), Next(0.4, 2.2), Next(12, 70)]);
            else
                rows.Add([Next(0, Width), Next(0, Height), Next(1, 3.4), Next(0.2, 1), Next(0, Math.Tau)]);
        }
        _particles = rows;
        _seeded = true;
    }

    private int CountFor(string kind) => kind switch
    {
        "rain" => Integer("count", 220, 10, 900),
        "bubbles" => Integer("count", 40, 4, 200),
        "starfield" => Integer("count", 320, 20, 2000),
        "waves" => Integer("count", 7, 2, 24),
        "grid" => Integer("cells", 24, 4, 120),
        _ => Integer("count", 150, 5, 800)
    };

    private void StepParticles(double dt)
    {
        var speed = Number("speed", 1, 0.05, 6);
        var drift = Number("drift", 0.25, -3, 3);
        var area = Math.Max(1, Width * (double)Height / (1920 * 1080));
        var radius = Number("link_distance", 130, 40, 400) * Math.Sqrt(area);
        foreach (var row in _particles)
        {
            row[0] += row[3] * speed * dt * 14;
            row[1] += drift * speed * dt * 10;
            row[4] += dt * 0.6;
            if (row[0] > Width + 10) row[0] = -10;
            else if (row[0] < -10) row[0] = Width + 10;
            if (row[1] > Height + 10) row[1] = -10;
            else if (row[1] < -10) row[1] = Height + 10;
        }
        _connectDistance = Spec.Boolean("connections", true) ? radius : 0;
    }

    private void StepRain(double dt)
    {
        var speed = Number("speed", 1, 0.1, 8);
        var slant = Number("slant", 0.22, -1, 1);
        foreach (var row in _particles)
        {
            row[1] += row[2] * speed * dt * 26;
            row[0] += row[2] * speed * dt * 26 * slant;
            if (row[1] > Height + 20)
            {
                row[1] = -20;
                row[0] = Next(-Width * 0.2, Width);
                row[2] = Next(6, 18);
            }
        }
    }

    private void StepBubbles(double dt)
    {
        var speed = Number("speed", 1, 0.1, 5);
        foreach (var row in _particles)
        {
            row[1] -= row[3] * speed * dt * 30;
            row[0] += Math.Sin(Time * row[4] + row[2]) * dt * 12;
            if (row[1] < -row[2] * 2)
            {
                row[1] = Height + row[2];
                row[0] = Next(0, Width);
            }
        }
    }

    private void StepStarfield(double dt)
    {
        var speed = Number("speed", 1, 0.05, 6);
        foreach (var row in _particles)
        {
            row[0] -= row[3] * speed * dt;
            if (row[0] < -2)
                row[0] = Width + 2;
        }
    }

    private double Number(string key, double fallback, double minimum, double maximum) =>
        SceneSpec.ClampNumber(Spec.Param(key, fallback), minimum, maximum, fallback);

    private int Integer(string key, int fallback, int minimum, int maximum) =>
        SceneSpec.ClampInt(Spec.Param(key, fallback), minimum, maximum, fallback);

    private double Next(double minimum, double maximum) => minimum + _random.NextDouble() * (maximum - minimum);
}

public readonly record struct RgbColor(byte R, byte G, byte B);
public readonly record struct RgbaColor(byte R, byte G, byte B, byte A);

public static class SceneColor
{
    public static RgbColor? Parse(string? value)
    {
        var text = value?.Trim() ?? "";
        byte red, green, blue;
        if (text.StartsWith('#'))
        {
            var digits = text[1..];
            if (digits.Length == 3)
                digits = string.Concat(digits.Select(character => new string(character, 2)));
            if (digits.Length >= 6 &&
                byte.TryParse(digits[..2], System.Globalization.NumberStyles.HexNumber, null, out red) &&
                byte.TryParse(digits[2..4], System.Globalization.NumberStyles.HexNumber, null, out green) &&
                byte.TryParse(digits[4..6], System.Globalization.NumberStyles.HexNumber, null, out blue))
                return new RgbColor(red, green, blue);
            return null;
        }

        var open = text.IndexOf('(');
        var close = text.IndexOf(')', open + 1);
        if (open < 0 || close < 0 ||
            !(text[..open].Equals("rgb", StringComparison.OrdinalIgnoreCase) ||
              text[..open].Equals("rgba", StringComparison.OrdinalIgnoreCase)))
            return null;
        var parts = text[(open + 1)..close].Split(',');
        if (parts.Length < 3 ||
            !byte.TryParse(parts[0].Trim(), out red) ||
            !byte.TryParse(parts[1].Trim(), out green) ||
            !byte.TryParse(parts[2].Trim(), out blue))
            return null;
        return new RgbColor(red, green, blue);
    }

    public static RgbaColor Hsv(double hue, double saturation, double value, double alpha = 1)
    {
        hue = ((hue % 360) + 360) % 360;
        var chroma = value * saturation;
        var x = chroma * (1 - Math.Abs((hue / 60 % 2) - 1));
        var m = value - chroma;
        var sector = (int)(hue / 60) % 6;
        var (red, green, blue) = sector switch
        {
            0 => (chroma, x, 0d),
            1 => (x, chroma, 0d),
            2 => (0d, chroma, x),
            3 => (0d, x, chroma),
            4 => (x, 0d, chroma),
            _ => (chroma, 0d, x)
        };
        return new RgbaColor(Channel(red + m), Channel(green + m), Channel(blue + m),
            (byte)Math.Clamp((int)(Math.Clamp(alpha, 0, 1) * 255), 0, 255));
    }

    private static byte Channel(double value) => (byte)Math.Clamp((int)(value * 255), 0, 255);
}

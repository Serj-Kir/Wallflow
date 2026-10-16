using System.Diagnostics;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Media;
using Avalonia.Threading;

namespace Wallflow.App.Core.Scenes;

public sealed class SceneControl : Control
{
    private SceneSimulator _simulator;
    private readonly DispatcherTimer _timer;
    private long _lastTick;

    public SceneControl()
        : this(SceneSpec.Builtin("particles"))
    {
    }

    public SceneControl(SceneSpec spec)
    {
        _simulator = new SceneSimulator(spec);
        _timer = new DispatcherTimer();
        _timer.Tick += Tick;
        UpdateInterval();
        ClipToBounds = true;
    }

    public SceneSpec Spec => _simulator.Spec;
    public bool IsRunning => _timer.IsEnabled;

    public void SetSpec(SceneSpec spec)
    {
        ArgumentNullException.ThrowIfNull(spec);
        _simulator = new SceneSimulator(spec, PixelSize(Bounds.Width), PixelSize(Bounds.Height));
        UpdateInterval();
        InvalidateVisual();
    }

    public void SetSource(string path) =>
        SetSpec(Path.GetExtension(path).Equals(".scene", StringComparison.OrdinalIgnoreCase) && File.Exists(path)
            ? SceneSpec.Load(path)
            : SceneSpec.Builtin("particles"));

    public void Start()
    {
        _lastTick = Stopwatch.GetTimestamp();
        _timer.Start();
        InvalidateVisual();
    }

    public void Pause() => _timer.Stop();

    public void SetMaxFps(int fps)
    {
        Spec.Fps = Math.Clamp(fps, 1, 60);
        UpdateInterval();
    }

    public void Stop()
    {
        Pause();
        _simulator.ResetTime();
        InvalidateVisual();
    }

    protected override void OnSizeChanged(SizeChangedEventArgs e)
    {
        base.OnSizeChanged(e);
        _simulator.Resize(PixelSize(e.NewSize.Width), PixelSize(e.NewSize.Height));
    }

    protected override void OnDetachedFromVisualTree(Avalonia.VisualTreeAttachmentEventArgs e)
    {
        Pause();
        base.OnDetachedFromVisualTree(e);
    }

    public override void Render(DrawingContext context)
    {
        base.Render(context);
        var bounds = new Rect(Bounds.Size);
        PaintBackground(context, bounds);
        switch (Spec.Kind)
        {
            case "starfield": PaintStarfield(context); break;
            case "waves": PaintWaves(context, bounds); break;
            case "rain": PaintRain(context); break;
            case "bubbles": PaintBubbles(context); break;
            case "gradient": PaintAnimatedGradient(context, bounds); break;
            case "plasma": PaintPlasma(context, bounds); break;
            case "breathing": PaintBreathing(context, bounds); break;
            case "grid": PaintGrid(context); break;
            default: PaintParticles(context); break;
        }
    }

    private void Tick(object? sender, EventArgs e)
    {
        var now = Stopwatch.GetTimestamp();
        var dt = (now - _lastTick) / (double)Stopwatch.Frequency;
        _lastTick = now;
        if (dt < 0 || dt > 1)
            dt = 1.0 / Math.Clamp(Spec.Fps, 1, 60);
        _simulator.Step(dt);
        InvalidateVisual();
    }

    private void UpdateInterval() =>
        _timer.Interval = TimeSpan.FromMilliseconds(Math.Max(1, 1000 / Math.Clamp(Spec.Fps, 1, 60)));

    private void PaintBackground(DrawingContext context, Rect bounds)
    {
        var palette = _simulator.Palette();
        IBrush brush = new SolidColorBrush(ToColor(palette[0]));
        if (Spec.Background.Kind.Equals("gradient", StringComparison.OrdinalIgnoreCase) && palette.Count > 1)
        {
            var angle = ((Spec.Background.Angle % 360) + 360) % 360;
            var radians = angle * Math.PI / 180;
            var dx = Math.Cos(radians);
            var dy = Math.Sin(radians);
            var start = new RelativePoint(dx < 0 ? 1 : 0, dy < 0 ? 1 : 0, RelativeUnit.Relative);
            var end = new RelativePoint(dx >= 0 ? 1 : 0, dy >= 0 ? 1 : 0, RelativeUnit.Relative);
            var stops = new GradientStops();
            for (var i = 0; i < palette.Count; i++)
                stops.Add(new GradientStop(ToColor(palette[i]), i / (double)(palette.Count - 1)));
            brush = new LinearGradientBrush { StartPoint = start, EndPoint = end, GradientStops = stops };
        }
        context.DrawRectangle(brush, null, bounds);
    }

    private void PaintParticles(DrawingContext context)
    {
        var rows = _simulator.Sample();
        var hue = Spec.Number("hue", 210);
        var saturation = Spec.Number("saturation", 0.65);
        var value = Spec.Number("value", 0.95);
        var pointSize = Spec.Number("point_size", 2.4);
        var alpha = Spec.Number("alpha", 0.85);
        var area = Math.Max(1, _simulator.Width * (double)_simulator.Height / (1920 * 1080));
        var radius = pointSize * Math.Sqrt(area);
        var link = _simulator.ConnectDistance;
        if (link > 0 && rows.Count <= 260)
        {
            var limit = link * link;
            for (var i = 0; i < rows.Count; i++)
            for (var j = i + 1; j < rows.Count; j++)
            {
                var a = rows[i];
                var b = rows[j];
                var dx = b[0] - a[0];
                var dy = b[1] - a[1];
                var distanceSquared = dx * dx + dy * dy;
                if (distanceSquared >= limit)
                    continue;
                var fade = 1 - Math.Sqrt(distanceSquared) / link;
                var color = SceneColor.Hsv(hue + 40 * fade, saturation, value, 0.10 * alpha * fade);
                context.DrawLine(new Pen(new SolidColorBrush(ToColor(color)), 1), new Point(a[0], a[1]), new Point(b[0], b[1]));
            }
        }
        foreach (var row in rows)
        {
            var color = SceneColor.Hsv(hue + row[4] * 40, saturation, value, alpha * row[3]);
            var size = radius * row[2] * 0.6;
            context.DrawEllipse(new SolidColorBrush(ToColor(color)), null,
                new Point(row[0], row[1]), size, size);
        }
    }

    private void PaintStarfield(DrawingContext context)
    {
        foreach (var row in _simulator.Sample())
        {
            var brightness = 0.35 + 0.65 * row[2] / 2.2;
            var radius = row[2];
            if (row[3] > 40)
            {
                var halo = new RgbaColor(200, 220, 255, (byte)(brightness * 60));
                context.DrawEllipse(new SolidColorBrush(ToColor(halo)), null,
                    new Point(row[0], row[1]), radius * 3.5, radius * 3.5);
            }
            var star = new RgbaColor(245, 248, 255, (byte)Math.Clamp((int)(brightness * 255), 0, 255));
            context.DrawEllipse(new SolidColorBrush(ToColor(star)), null,
                new Point(row[0], row[1]), radius, radius);
        }
    }

    private void PaintRain(DrawingContext context)
    {
        var color = SceneColor.Hsv(Spec.Number("hue", 200), 0.15, 0.95, 0.42);
        var pen = new Pen(new SolidColorBrush(ToColor(color)), 1.4);
        foreach (var row in _simulator.Sample())
            context.DrawLine(pen, new Point(row[0], row[1]),
                new Point(row[0] - row[2] * 0.22, row[1] - row[2] * 2.6));
    }

    private void PaintBubbles(DrawingContext context)
    {
        foreach (var row in _simulator.Sample())
        {
            var (x, y, radius, _, phase) = (row[0], row[1], row[2], row[3], row[4]);
            var shade = 0.5 + 0.4 * Math.Sin(phase + _simulator.Time);
            var outer = new RgbaColor(190, 215, 255, (byte)(18 + shade * 26));
            var inner = new RgbaColor(255, 255, 255, (byte)(70 + shade * 60));
            context.DrawEllipse(new SolidColorBrush(ToColor(outer)), null, new Point(x, y), radius, radius);
            context.DrawEllipse(new SolidColorBrush(ToColor(inner)), null,
                new Point(x - radius * 0.3, y - radius * 0.3), radius * 0.35, radius * 0.35);
        }
    }

    private void PaintWaves(DrawingContext context, Rect bounds)
    {
        var count = _simulator.Sample().Count;
        var amplitude = Spec.Number("amplitude", 60);
        var wavelength = Math.Max(1, Spec.Number("wavelength", 420));
        var baseY = _simulator.Height * 0.72;
        var bottom = _simulator.Height + amplitude * 2;
        var hue = Spec.Number("hue", 200);
        for (var index = 0; index < count; index++)
        {
            var offset = index * (_simulator.Height * 0.055);
            var alpha = 0.30 - index * 0.03;
            if (alpha <= 0.02)
                break;
            var geometry = new StreamGeometry();
            using (var path = geometry.Open())
            {
                const int steps = 56;
                for (var step = 0; step <= steps; step++)
                {
                    var x = bounds.Left + bounds.Width * step / steps;
                    var phase = _simulator.Time * 0.9 + index * 0.55;
                    var y = baseY + offset +
                            Math.Sin(x / wavelength * Math.Tau + phase) * amplitude;
                    if (step == 0)
                        path.BeginFigure(new Point(x, y), true);
                    else
                        path.LineTo(new Point(x, y));
                }
                path.LineTo(new Point(bounds.Right, bottom));
                path.LineTo(new Point(bounds.Left, bottom));
                path.EndFigure(true);
            }
            var color = SceneColor.Hsv(hue + index * 22, 0.6, 0.9, alpha);
            context.DrawGeometry(new SolidColorBrush(ToColor(color)), null, geometry);
        }
    }

    private void PaintAnimatedGradient(DrawingContext context, Rect bounds)
    {
        var palette = _simulator.Palette();
        var speed = Spec.Number("speed", 0.15);
        var shift = (_simulator.Time * speed) % (palette.Count * 100);
        var stops = new GradientStops();
        for (var i = 0; i < palette.Count; i++)
        {
            var position = ((i * 100 + shift) / Math.Max(1, palette.Count * 100 - 1)) % 1;
            stops.Add(new GradientStop(ToColor(palette[i]), Math.Clamp(position, 0, 1)));
        }
        context.DrawRectangle(new LinearGradientBrush
        {
            StartPoint = RelativePoint.TopLeft,
            EndPoint = RelativePoint.BottomRight,
            GradientStops = stops
        }, null, bounds);
    }

    private void PaintPlasma(DrawingContext context, Rect bounds)
    {
        var scale = Spec.Number("scale", 3);
        var cell = Math.Max(6, Spec.Number("cell", 14));
        var columns = (int)(bounds.Width / cell) + 1;
        var rows = (int)(bounds.Height / cell) + 1;
        var hue = Spec.Number("hue", 260);
        for (var row = 0; row < rows; row++)
        for (var column = 0; column < columns; column++)
        {
            var x = column * cell;
            var y = row * cell;
            var value = (Math.Sin(x / Math.Max(1, bounds.Width) * scale * Math.Tau + _simulator.Time)
                         + Math.Sin(y / Math.Max(1, bounds.Height) * scale * Math.Tau + _simulator.Time * 1.3)
                         + Math.Sin((x + y) / Math.Max(1, bounds.Width) * scale * Math.Tau + _simulator.Time * 0.7)) / 3;
            var color = SceneColor.Hsv(hue + value * 90, 0.7, 0.95, 0.10 + 0.18 * (value + 1) / 2);
            context.DrawRectangle(new SolidColorBrush(ToColor(color)), null,
                new Rect(x, y, cell + 1, cell + 1));
        }
    }

    private void PaintBreathing(DrawingContext context, Rect bounds)
    {
        var palette = _simulator.Palette();
        var first = palette[0];
        var second = palette[Math.Min(1, palette.Count - 1)];
        var phase = (Math.Sin(_simulator.Time * Spec.Number("speed", 0.8)) + 1) / 2;
        var start = ToColor(new RgbaColor(first.R, first.G, first.B, (byte)(255 * (0.35 + 0.65 * phase))));
        var end = ToColor(new RgbaColor(second.R, second.G, second.B, (byte)(255 * (0.35 + 0.65 * (1 - phase)))));
        var brush = new RadialGradientBrush
        {
            Center = new RelativePoint(0.5, 0.5, RelativeUnit.Relative),
            GradientOrigin = new RelativePoint(0.5, 0.5, RelativeUnit.Relative),
            RadiusX = new RelativeScalar(0.75, RelativeUnit.Relative),
            RadiusY = new RelativeScalar(0.75, RelativeUnit.Relative),
            GradientStops = new GradientStops { new GradientStop(start, 0), new GradientStop(end, 1) }
        };
        context.DrawRectangle(brush, null, bounds);
    }

    private void PaintGrid(DrawingContext context)
    {
        var rows = _simulator.Sample();
        if (rows.Count == 0)
            return;
        var cellHeight = _simulator.Height / (double)rows.Count;
        var hue = Spec.Number("hue", 200);
        for (var index = 0; index < rows.Count; index++)
        {
            var offset = (_simulator.Time * Spec.Number("speed", 0.6) * cellHeight) % (cellHeight * 2);
            var y = rows[index][1] - offset;
            while (y < -cellHeight)
                y += cellHeight * 2;
            var alpha = 0.10 + 0.28 * Math.Abs(Math.Sin(index * 0.4 + _simulator.Time));
            var color = SceneColor.Hsv(hue + index * 8, 0.7, 0.95, alpha);
            context.DrawRectangle(new SolidColorBrush(ToColor(color)), null,
                new Rect(0, y, _simulator.Width, cellHeight * 0.8));
        }
    }

    private int PixelSize(double value) => Math.Max(1, (int)Math.Ceiling(value));

    private static Color ToColor(RgbColor color) => Color.FromRgb(color.R, color.G, color.B);
    private static Color ToColor(RgbaColor color) => Color.FromArgb(color.A, color.R, color.G, color.B);
}

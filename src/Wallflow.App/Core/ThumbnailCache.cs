using Avalonia.Media.Imaging;
using Avalonia.Threading;
using SkiaSharp;

namespace Wallflow.App.Core;

/// <summary>
/// Disk-backed thumbnail cache. Thumbnails are generated on a worker thread into
/// <c>thumbs/&lt;id&gt;_&lt;size&gt;_&lt;mtime&gt;.png</c> and only decoded on the UI
/// thread once the file exists, so the library list stays responsive.
/// </summary>
public sealed class ThumbnailCache
{
    private const int MemoryCapacity = 128;
    private static readonly SKColor PlaceholderBackground = new(0x27, 0x29, 0x32);
    private static readonly SKColor PlaceholderForeground = new(0x7d, 0x84, 0x94);

    private readonly object _gate = new();
    private readonly Dictionary<string, Bitmap> _memory = new(StringComparer.Ordinal);
    private readonly LinkedList<string> _recency = new();
    private readonly HashSet<string> _pending = new(StringComparer.Ordinal);
    private readonly SemaphoreSlim _generationSlots = new(2);
    private readonly int _size;

    public ThumbnailCache(int size) => _size = Math.Clamp(size, 80, 512);

    public int Size => _size;

    /// <summary>
    /// Fills <see cref="Wallpaper.Thumbnail"/> for a wallpaper, loading it from memory,
    /// from disk, or scheduling generation in the background.
    /// </summary>
    public void Attach(Wallpaper wallpaper)
    {
        var key = KeyFor(wallpaper);
        if (TryGetFromMemory(key) is { } cached)
        {
            wallpaper.Thumbnail = cached;
            return;
        }

        var path = Path.Combine(WallflowPaths.ThumbnailDirectory, key + ".png");
        if (File.Exists(path))
        {
            Load(wallpaper, key, path);
            return;
        }

        lock (_gate)
        {
            if (!_pending.Add(key))
                return;
        }

        Task.Run(() =>
        {
            _generationSlots.Wait();
            try
            {
                GenerateFile(wallpaper, path);
            }
            catch (Exception exception) when (
                exception is IOException or UnauthorizedAccessException or InvalidOperationException or
                    ArgumentException)
            {
                Log.Warn($"Could not generate a thumbnail for {wallpaper.Path}", exception);
            }
            finally
            {
                _generationSlots.Release();
                lock (_gate)
                    _pending.Remove(key);
            }

            if (!File.Exists(path))
                return;
            Dispatcher.UIThread.Post(() =>
            {
                if (wallpaper.Thumbnail is null)
                    Load(wallpaper, key, path);
            });
        });
    }

    /// <summary>Drops every cached bitmap; used when the thumbnail size changes.</summary>
    public void Clear()
    {
        lock (_gate)
        {
            foreach (var bitmap in _memory.Values)
                bitmap.Dispose();
            _memory.Clear();
            _recency.Clear();
        }
    }

    private void Load(Wallpaper wallpaper, string key, string path)
    {
        try
        {
            var bitmap = new Bitmap(path);
            lock (_gate)
            {
                if (_memory.TryGetValue(key, out var existing))
                {
                    bitmap.Dispose();
                    bitmap = existing;
                }
                else
                {
                    _memory[key] = bitmap;
                    _recency.AddLast(key);
                    while (_recency.Count > MemoryCapacity)
                    {
                        var oldest = _recency.First!.Value;
                        _recency.RemoveFirst();
                        if (_memory.Remove(oldest, out var evicted))
                            evicted.Dispose();
                    }
                }
            }
            wallpaper.Thumbnail = bitmap;
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or ArgumentException)
        {
            Log.Warn($"Could not load a thumbnail for {wallpaper.Path}", exception);
        }
    }

    private Bitmap? TryGetFromMemory(string key)
    {
        lock (_gate)
        {
            if (!_memory.TryGetValue(key, out var bitmap))
                return null;
            _recency.Remove(key);
            _recency.AddLast(key);
            return bitmap;
        }
    }

    private string KeyFor(Wallpaper wallpaper)
    {
        long stamp = 0;
        try
        {
            if (File.Exists(wallpaper.Path))
                stamp = File.GetLastWriteTimeUtc(wallpaper.Path).Ticks;
            else if (Directory.Exists(wallpaper.Path))
                stamp = Directory.GetLastWriteTimeUtc(wallpaper.Path).Ticks;
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
            stamp = 0;
        }

        return $"{wallpaper.Id}_{_size}_{stamp}";
    }

    private void GenerateFile(Wallpaper wallpaper, string target)
    {
        Directory.CreateDirectory(WallflowPaths.ThumbnailDirectory);
        using var bitmap = new SKBitmap(_size, _size);
        using (var canvas = new SKCanvas(bitmap))
        {
            canvas.Clear(PlaceholderBackground);
            var drawn = wallpaper.WallpaperType == WallpaperKind.Image && File.Exists(wallpaper.Path) &&
                TryDrawImage(canvas, wallpaper.Path);
            if (!drawn)
                DrawKindGlyph(canvas, wallpaper.WallpaperType);
        }

        var temporary = target + ".tmp";
        using (var image = SKImage.FromBitmap(bitmap))
        using (var data = image.Encode(SKEncodedImageFormat.Png, 90))
        using (var stream = File.Create(temporary))
            data.SaveTo(stream);
        File.Move(temporary, target, overwrite: true);
    }

    private bool TryDrawImage(SKCanvas canvas, string path)
    {
        using var source = SKBitmap.Decode(path);
        if (source is null || source.Width <= 0 || source.Height <= 0)
            return false;

        var side = Math.Min(source.Width, source.Height);
        var sourceRect = new SKRect(
            (source.Width - side) / 2f,
            (source.Height - side) / 2f,
            (source.Width + side) / 2f,
            (source.Height + side) / 2f);
        var destination = new SKRect(0, 0, _size, _size);
        using var image = SKImage.FromBitmap(source);
        canvas.DrawImage(image, sourceRect, destination, new SKSamplingOptions(SKFilterMode.Linear, SKMipmapMode.Linear), null);
        return true;
    }

    private void DrawKindGlyph(SKCanvas canvas, WallpaperKind kind)
    {
        using var paint = new SKPaint
        {
            Color = PlaceholderForeground,
            IsAntialias = true,
            Style = SKPaintStyle.Stroke,
            StrokeWidth = Math.Max(2f, _size / 40f),
            StrokeCap = SKStrokeCap.Round,
            StrokeJoin = SKStrokeJoin.Round
        };
        var center = _size / 2f;
        var unit = _size / 4f;
        switch (kind)
        {
            case WallpaperKind.Video:
                using (var path = new SKPath())
                {
                    path.MoveTo(center - unit * 0.7f, center - unit);
                    path.LineTo(center + unit, center);
                    path.LineTo(center - unit * 0.7f, center + unit);
                    path.Close();
                    paint.Style = SKPaintStyle.Fill;
                    canvas.DrawPath(path, paint);
                }
                break;
            case WallpaperKind.Scene:
                using (var path = new SKPath())
                {
                    path.MoveTo(center, center - unit);
                    path.LineTo(center + unit * 0.55f, center - unit * 0.15f);
                    path.LineTo(center + unit, center + unit);
                    path.LineTo(center - unit, center + unit);
                    path.LineTo(center - unit * 0.55f, center - unit * 0.15f);
                    path.Close();
                    canvas.DrawPath(path, paint);
                }
                break;
            case WallpaperKind.Web:
                canvas.DrawRoundRect(
                    new SKRect(center - unit, center - unit * 0.8f, center + unit, center + unit * 0.8f),
                    unit * 0.2f, unit * 0.2f, paint);
                canvas.DrawLine(center - unit * 0.45f, center - unit * 0.55f, center, center, paint);
                canvas.DrawLine(center, center, center - unit * 0.45f, center + unit * 0.55f, paint);
                canvas.DrawLine(center + unit * 0.25f, center - unit * 0.15f, center + unit * 0.75f, center - unit * 0.15f, paint);
                break;
            default:
                canvas.DrawRoundRect(
                    new SKRect(center - unit, center - unit, center + unit, center + unit),
                    unit * 0.25f, unit * 0.25f, paint);
                canvas.DrawLine(center - unit * 0.6f, center + unit * 0.3f, center - unit * 0.1f, center - unit * 0.35f, paint);
                canvas.DrawLine(center - unit * 0.1f, center - unit * 0.35f, center + unit * 0.65f, center + unit * 0.45f, paint);
                break;
        }
    }
}

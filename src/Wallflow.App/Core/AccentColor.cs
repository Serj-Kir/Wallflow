using SkiaSharp;

namespace Wallflow.App.Core;

/// <summary>
/// Picks an accent colour from a wallpaper by sampling its dominant saturated hue.
/// The bucketing mirrors the original algorithm so the same wallpaper yields the
/// same colour in both implementations.
/// </summary>
public static class AccentColor
{
    public const string Fallback = "#20202a";
    private const int SampleSize = 96;
    private const int HueBins = 24;
    private const int SaturationBins = 6;
    private const int ValueBins = 6;

    public static string Dominant(string? path, string fallback = Fallback)
    {
        if (string.IsNullOrEmpty(path) || !File.Exists(path))
            return fallback;

        try
        {
            using var source = SKBitmap.Decode(path);
            if (source is null || source.Width <= 0 || source.Height <= 0)
                return fallback;

            using var sample = new SKBitmap(SampleSize, SampleSize);
            using (var canvas = new SKCanvas(sample))
            {
                canvas.Clear(SKColors.Black);
                var scale = Math.Min(SampleSize / (float)source.Width, SampleSize / (float)source.Height);
                var width = source.Width * scale;
                var height = source.Height * scale;
                var destination = new SKRect(
                    (SampleSize - width) / 2f,
                    (SampleSize - height) / 2f,
                    (SampleSize + width) / 2f,
                    (SampleSize + height) / 2f);
                using var image = SKImage.FromBitmap(source);
                canvas.DrawImage(image, destination, new SKSamplingOptions(SKFilterMode.Linear), null);
            }

            var bestScore = -1d;
            var bestHueBin = -1;
            var bestSaturationBin = 0;
            var bestValueBin = 0;
            for (var y = 0; y < SampleSize; y += 2)
            {
                for (var x = 0; x < SampleSize; x += 2)
                {
                    var pixel = sample.GetPixel(x, y);
                    if (pixel.Alpha < 32)
                        continue;

                    pixel.ToHsv(out var hue, out var saturationPercent, out var valuePercent);
                    // SkiaSharp reports saturation and value as percentages (0..100).
                    var saturation = saturationPercent / 100f;
                    var value = valuePercent / 100f;
                    if (value is < 0.14f or > 0.96f || saturation < 0.12f)
                        continue;

                    var hueBin = Math.Clamp((int)(hue / 360f * HueBins), 0, HueBins - 1);
                    var saturationBin = Math.Clamp((int)(saturation * SaturationBins), 0, SaturationBins - 1);
                    var valueBin = Math.Clamp((int)(value * ValueBins), 0, ValueBins - 1);
                    var score = (0.35 + saturation) * (0.35 + value);
                    if (score <= bestScore)
                        continue;

                    bestScore = score;
                    bestHueBin = hueBin;
                    bestSaturationBin = saturationBin;
                    bestValueBin = valueBin;
                }
            }

            if (bestHueBin < 0)
                return fallback;

            var chosenHue = (bestHueBin + 0.5f) * 360f / HueBins;
            var chosenSaturation = Math.Min(1f, 0.42f + bestSaturationBin * 0.55f / SaturationBins);
            var chosenValue = Math.Min(1f, 0.55f + bestValueBin * 0.45f / ValueBins);
            var color = SKColor.FromHsv(chosenHue, chosenSaturation * 100f, chosenValue * 100f);
            return WallpaperBackendHelpers.NormalizeHex($"#{color.Red:X2}{color.Green:X2}{color.Blue:X2}");
        }
        catch (Exception exception) when (
            exception is IOException or UnauthorizedAccessException or ArgumentException)
        {
            Log.Warn($"Could not extract an accent colour from {path}", exception);
            return fallback;
        }
    }

    /// <summary>Black or white text for a given background, by Rec. 709 luma.</summary>
    public static string ContrastingText(string hexColor)
    {
        var normalized = WallpaperBackendHelpers.NormalizeHex(hexColor);
        if (normalized.Length != 7)
            return "#ffffff";
        var red = Convert.ToInt32(normalized.Substring(1, 2), 16) / 255d;
        var green = Convert.ToInt32(normalized.Substring(3, 2), 16) / 255d;
        var blue = Convert.ToInt32(normalized.Substring(5, 2), 16) / 255d;
        var luma = 0.2126 * red + 0.7152 * green + 0.0722 * blue;
        return luma > 0.55 ? "#000000" : "#ffffff";
    }
}

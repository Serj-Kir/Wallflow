using LibVLCSharp.Shared;
using SkiaSharp;

namespace Wallflow.App.Core;

public readonly record struct MediaSize(int Width, int Height)
{
    public override string ToString() => $"{Width}x{Height}";
}

/// <summary>
/// Reads the native resolution of wallpapers so the UI can warn when a video is
/// smaller than the monitor it is stretched across.
/// </summary>
public static class MediaInfo
{
    public static MediaSize? TryGetImageSize(string path)
    {
        try
        {
            if (!File.Exists(path))
                return null;
            using var codec = SKCodec.Create(path);
            if (codec is null)
                return null;
            var info = codec.Info;
            return info.Width > 0 && info.Height > 0 ? new MediaSize(info.Width, info.Height) : null;
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
            Log.Warn($"Could not read image size of {path}", exception);
            return null;
        }
    }

    public static async Task<MediaSize?> TryGetVideoSizeAsync(string path, int timeoutMs = 4000)
    {
        if (!File.Exists(path) || !VideoRuntime.IsAvailable)
            return null;
        Media? media = null;
        try
        {
            media = new Media(VideoRuntime.Shared, path, FromType.FromPath);
            var status = await media.Parse(MediaParseOptions.ParseLocal, timeoutMs).ConfigureAwait(false);
            if (status != MediaParsedStatus.Done)
                return null;
            foreach (var track in media.Tracks)
            {
                if (track.TrackType != TrackType.Video)
                    continue;
                var video = track.Data.Video;
                if (video.Width > 0 && video.Height > 0)
                    return new MediaSize((int)video.Width, (int)video.Height);
            }
            return null;
        }
        catch (Exception exception) when (
            exception is VLCException or DllNotFoundException or EntryPointNotFoundException or
                InvalidOperationException)
        {
            Log.Warn($"Could not probe video resolution of {path}", exception);
            return null;
        }
        finally
        {
            media?.Dispose();
        }
    }

    /// <summary>Native resolution for any wallpaper kind, when it can be determined.</summary>
    public static Task<MediaSize?> TryGetSizeAsync(Wallpaper wallpaper) => wallpaper.WallpaperType switch
    {
        WallpaperKind.Video => TryGetVideoSizeAsync(wallpaper.Path),
        WallpaperKind.Image => Task.FromResult(TryGetImageSize(wallpaper.Path)),
        _ => Task.FromResult<MediaSize?>(null)
    };

    /// <summary>Formats a size the way the desktop tools report it.</summary>
    public static string Describe(MediaSize size) => size switch
    {
        { Width: 1920, Height: 1080 } => "1920x1080 (1080p)",
        { Width: 2560, Height: 1440 } => "2560x1440 (1440p)",
        { Width: 3840, Height: 2160 } => "3840x2160 (4K)",
        { Width: 7680, Height: 4320 } => "7680x4320 (8K)",
        _ => size.ToString()
    };
}

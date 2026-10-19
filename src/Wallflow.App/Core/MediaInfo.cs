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
/// <remarks>
/// Video resolutions during playback come from the player itself (see
/// <see cref="LiveWallpaperControl"/>). Parsing a file with libvlc is only used for
/// the library window, and it follows two rules learned from crashes on Windows:
/// the parse runs on <see cref="VideoRuntime.Metadata"/> so it never shares native
/// state with a playing surface, and the parsed <see cref="Media"/> is never
/// released — freeing it while libvlc still used it ended the process with an
/// access violation. Each parsed handle is tiny, so they live until the process
/// exits and the result is cached instead.
/// </remarks>
public static class MediaInfo
{
    private static readonly SemaphoreSlim ParseGate = new(1, 1);
    private static readonly Dictionary<string, MediaSize?> SizeCache = new(StringComparer.Ordinal);
    private static readonly List<Media> ParsedMedia = [];

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
        var key = CacheKey(path);
        lock (SizeCache)
        {
            if (SizeCache.TryGetValue(key, out var cached))
                return cached;
        }

        if (!IsReadable(path) || !VideoRuntime.IsAvailable)
            return null;

        MediaSize? size;
        await ParseGate.WaitAsync().ConfigureAwait(false);
        try
        {
            size = await ParseVideoSizeAsync(path, timeoutMs).ConfigureAwait(false);
        }
        catch (Exception exception) when (
            exception is VLCException or DllNotFoundException or EntryPointNotFoundException or
                InvalidOperationException or TypeInitializationException)
        {
            Log.Warn($"Could not probe video resolution of {path}", exception);
            size = null;
        }
        finally
        {
            ParseGate.Release();
        }

        lock (SizeCache)
            SizeCache[key] = size;
        return size;
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

    private static async Task<MediaSize?> ParseVideoSizeAsync(string path, int timeoutMs)
    {
        var media = new Media(VideoRuntime.Metadata, path, FromType.FromPath);
        // Deliberately never disposed: see the remarks on this class.
        lock (ParsedMedia)
            ParsedMedia.Add(media);

        var status = await media.Parse(MediaParseOptions.ParseLocal, timeoutMs).ConfigureAwait(false);
        if (status != MediaParsedStatus.Done)
        {
            Log.Warn($"Could not parse the video {path} (status {status}).");
            return null;
        }

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

    private static bool IsReadable(string path)
    {
        try
        {
            return File.Exists(path) && new FileInfo(path).Length > 0;
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
            return false;
        }
    }

    private static string CacheKey(string path)
    {
        try
        {
            return $"{Path.GetFullPath(path)}|{File.GetLastWriteTimeUtc(path).Ticks}";
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
            return Path.GetFullPath(path);
        }
    }
}

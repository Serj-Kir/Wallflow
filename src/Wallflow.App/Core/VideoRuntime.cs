using LibVLCSharp.Shared;

namespace Wallflow.App.Core;

/// <summary>
/// One process-wide LibVLC instance. Creating more than one is expensive and
/// unnecessary: playback, thumbnails and metadata probing share it.
/// </summary>
public static class VideoRuntime
{
    private static readonly Lazy<LibVLC> Instance = new(Create);

    public static LibVLC Shared => Instance.Value;

    public static bool IsAvailable
    {
        get
        {
            try
            {
                _ = Shared;
                return true;
            }
            catch (Exception exception) when (
                exception is DllNotFoundException or EntryPointNotFoundException or VLCException or
                    InvalidOperationException or TypeInitializationException)
            {
                Log.Warn("LibVLC is unavailable", exception);
                return false;
            }
        }
    }

    private static LibVLC Create()
    {
        global::LibVLCSharp.Shared.Core.Initialize();
        return new LibVLC("--no-video-title-show");
    }
}

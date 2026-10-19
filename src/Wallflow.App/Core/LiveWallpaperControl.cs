using Avalonia;
using Avalonia.Controls;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Platform;
using Avalonia.Threading;
using LibVLCSharp.Avalonia;
using LibVLCSharp.Shared;
using SkiaSharp;
using Wallflow.App.Core.Scenes;

namespace Wallflow.App.Core;

public sealed class LiveWallpaperControl : UserControl, IDisposable
{
    private const string WebApiScript = """
        window.wallflow = window.wallflow || {};
        Object.defineProperty(window.wallflow, 'version', { get: function() { return '1.0'; } });
        window.__wfPause = window.__wfPause || [];
        window.__wfResume = window.__wfResume || [];
        window.wallflow.onPause = function(fn) { window.__wfPause.push(fn); };
        window.wallflow.onResume = function(fn) { window.__wfResume.push(fn); };
        """;
    private const string WebPauseScript =
        "window.__wfPause.forEach(function(fn) { try { fn(); } catch (e) { console.error(e); } });";
    private const string WebResumeScript =
        "window.__wfResume.forEach(function(fn) { try { fn(); } catch (e) { console.error(e); } });";

    private static readonly Lazy<LibVLC> SharedLibVlc = new(() => VideoRuntime.Shared);
    private readonly Wallpaper _wallpaper;
    private bool _loopVideos;
    private bool _videoAudio;
    private int _maxFps;
    private Control? _renderer;
    private Bitmap? _bitmap;
    private AnimatedWallpaperImageRenderer? _animatedImageRenderer;
    private MediaPlayer? _mediaPlayer;
    private Media? _media;
    private bool _started;
    private bool _videoPlayStarted;
    private bool _pauseRequested;
    private bool _webPageReady;
    private bool _disposed;

    public LiveWallpaperControl(
        Wallpaper wallpaper,
        bool loopVideos = true,
        bool videoAudio = false,
        int maxFps = 60)
    {
        _wallpaper = wallpaper ?? throw new ArgumentNullException(nameof(wallpaper));
        _loopVideos = loopVideos;
        _videoAudio = videoAudio;
        _maxFps = Math.Clamp(maxFps, 1, 60);
        Background = Brushes.Black;
        HorizontalAlignment = Avalonia.Layout.HorizontalAlignment.Stretch;
        VerticalAlignment = Avalonia.Layout.VerticalAlignment.Stretch;
        ClipToBounds = true;
    }

    public event EventHandler<string>? PlaybackError;

    /// <summary>Raised with the source resolution once a video has been probed.</summary>
    public event EventHandler<MediaSize>? SourceResolutionResolved;

    public WallpaperKind WallpaperType => _wallpaper.WallpaperType;
    public MediaSize? SourceSize { get; private set; }

    public async Task StartAsync()
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        if (_started)
            return;
        try
        {
            switch (_wallpaper.WallpaperType)
            {
                case WallpaperKind.Image:
                    LoadImage();
                    break;
                case WallpaperKind.Scene:
                    LoadScene();
                    break;
                case WallpaperKind.Video:
                    await LoadVideoAsync();
                    break;
                case WallpaperKind.Web:
                    await LoadWebAsync();
                    break;
                default:
                    throw new NotSupportedException($"Unsupported wallpaper type: {_wallpaper.Kind}");
            }

            _started = true;
            _animatedImageRenderer?.Start();
            if (_renderer is VideoView)
                StartVideo();
            if (_pauseRequested)
                Pause();
        }
        catch (Exception exception) when (
            exception is IOException or UnauthorizedAccessException or InvalidDataException or
                InvalidOperationException or NotSupportedException or System.Text.Json.JsonException or VLCException)
        {
            SetError(exception.Message);
            throw;
        }
    }

    public void Pause()
    {
        _pauseRequested = true;
        if (_mediaPlayer?.IsPlaying == true)
            _mediaPlayer.Pause();
        if (_renderer is SceneControl scene)
            scene.Pause();
        _animatedImageRenderer?.Pause();
        if (_renderer is NativeWebView webView)
        {
            if (_webPageReady)
                InvokeWebScript(webView, WebPauseScript);
            webView.IsVisible = false;
        }
    }

    public void Resume()
    {
        _pauseRequested = false;
        if (_mediaPlayer is not null && !_mediaPlayer.IsPlaying)
        {
            if (_videoPlayStarted)
                _mediaPlayer.Play();
            else
                StartVideo();
        }
        if (_renderer is SceneControl scene)
            scene.Start();
        _animatedImageRenderer?.Start();
        if (_renderer is NativeWebView webView)
        {
            webView.IsVisible = true;
            if (_webPageReady)
                InvokeWebScript(webView, WebResumeScript);
        }
    }

    public void Stop()
    {
        _started = false;
        _videoPlayStarted = false;
        _pauseRequested = false;
        _mediaPlayer?.Stop();
        if (_renderer is SceneControl scene)
            scene.Stop();
        _animatedImageRenderer?.Stop();
        if (_renderer is NativeWebView webView)
        {
            _webPageReady = false;
            webView.Source = new Uri("about:blank");
        }
    }

    public void UpdateSettings(bool loopVideos, bool videoAudio, int maxFps)
    {
        _loopVideos = loopVideos;
        _videoAudio = videoAudio;
        _maxFps = Math.Clamp(maxFps, 1, 60);
        if (_mediaPlayer is not null)
            _mediaPlayer.Volume = _videoAudio ? 50 : 0;
        if (_renderer is SceneControl scene)
            scene.SetMaxFps(_maxFps);
    }

    public void Dispose()
    {
        if (_disposed)
            return;

        Stop();
        if (_mediaPlayer is not null)
        {
            _mediaPlayer.Vout -= OnVideoOutput;
            _mediaPlayer.EncounteredError -= OnVideoFailed;
            _mediaPlayer.EndReached -= OnVideoEnded;
            _mediaPlayer.Dispose();
        }
        _media?.Dispose();
        _animatedImageRenderer?.Dispose();
        _bitmap?.Dispose();
        _disposed = true;
    }

    private void LoadImage()
    {
        if (!File.Exists(_wallpaper.Path))
            throw new FileNotFoundException("Wallpaper image does not exist.", _wallpaper.Path);
        if (string.Equals(Path.GetExtension(_wallpaper.Path), ".gif", StringComparison.OrdinalIgnoreCase))
        {
            _animatedImageRenderer = AnimatedWallpaperImageRenderer.TryCreate(
                _wallpaper.Path, _wallpaper.DisplayFit, _wallpaper.DisplayAlignment);
            if (_animatedImageRenderer is not null)
            {
                _renderer = _animatedImageRenderer;
                Content = _renderer;
                return;
            }
        }
        _bitmap = new Bitmap(_wallpaper.Path);
        _renderer = new WallpaperImageRenderer(_bitmap, _wallpaper.DisplayFit, _wallpaper.DisplayAlignment);
        Content = _renderer;
    }

    private void LoadScene()
    {
        var scene = new SceneControl(SceneSpec.Load(_wallpaper.Path));
        scene.SetMaxFps(_maxFps);
        _renderer = scene;
        Content = scene;
        scene.Start();
    }

    private async Task LoadVideoAsync()
    {
        if (!File.Exists(_wallpaper.Path))
            throw new FileNotFoundException("Video wallpaper does not exist.", _wallpaper.Path);
        if (new FileInfo(_wallpaper.Path).Length == 0)
            throw new InvalidDataException($"Video wallpaper file is empty: {_wallpaper.Path}");

        // Starting LibVLC loads hundreds of plug-ins, which can take seconds on a cold
        // start, so it is never done on the UI thread.
        var libVlc = await Task.Run(() => SharedLibVlc.Value);
        _mediaPlayer = new MediaPlayer(libVlc)
        {
            Volume = _videoAudio ? 50 : 0
        };
        _mediaPlayer.EndReached += OnVideoEnded;
        _mediaPlayer.EncounteredError += OnVideoFailed;
        _mediaPlayer.Vout += OnVideoOutput;
        _media = new Media(libVlc, _wallpaper.Path, FromType.FromPath);
        var view = new VideoView { MediaPlayer = _mediaPlayer };
        view.AttachedToVisualTree += (_, _) => Dispatcher.UIThread.Post(StartVideo);
        _renderer = view;
        Content = view;
    }

    /// <summary>
    /// Takes the source resolution from the player that is already decoding the video.
    /// Parsing the file next to a running player crashed native libvlc, and the player
    /// knows its own size at no extra cost.
    /// </summary>
    private void OnVideoOutput(object? sender, MediaPlayerVoutEventArgs e)
    {
        if (e.Count <= 0 || _mediaPlayer is null)
            return;
        _mediaPlayer.Vout -= OnVideoOutput;
        _ = ReadVideoSizeAsync();
    }

    private async Task ReadVideoSizeAsync()
    {
        for (var attempt = 0; attempt < 20 && !_disposed; attempt++)
        {
            var player = _mediaPlayer;
            if (player is null)
                return;
            try
            {
                uint width = 0;
                uint height = 0;
                if (player.Size(0, ref width, ref height) && width > 0 && height > 0)
                {
                    ReportSourceSize(new MediaSize((int)width, (int)height));
                    return;
                }
            }
            catch (Exception exception) when (exception is VLCException or ObjectDisposedException)
            {
                return;
            }

            await Task.Delay(150).ConfigureAwait(false);
        }
    }

    private void ReportSourceSize(MediaSize size) => Dispatcher.UIThread.Post(() =>
    {
        if (_disposed)
            return;
        SourceSize = size;
        SourceResolutionResolved?.Invoke(this, size);
    });

    private void OnVideoFailed(object? sender, EventArgs e) =>
        SetError($"LibVLC could not play the video: {_wallpaper.Path}");

    private Task LoadWebAsync()
    {
        var project = WebWallpaperProject.Load(_wallpaper.Path);
        if (!project.IsValid)
            throw new FileNotFoundException("HTML wallpaper must contain an index.html file.", project.EntryPath);
        if (project.MetadataError is { } metadataError)
            SetError(metadataError);

        var view = new NativeWebView
        {
            Background = Brushes.Black,
            HorizontalAlignment = Avalonia.Layout.HorizontalAlignment.Stretch,
            VerticalAlignment = Avalonia.Layout.VerticalAlignment.Stretch
        };
        view.NavigationCompleted += (_, args) =>
        {
            if (!args.IsSuccess)
            {
                _webPageReady = false;
                SetError($"HTML wallpaper failed to load: {args.Request}");
                return;
            }

            _webPageReady = true;
            InvokeWebScript(view, WebApiScript);
            if (_pauseRequested)
                InvokeWebScript(view, WebPauseScript);
        };
        view.AdapterCreated += (_, _) =>
        {
            if (view.AdapterInfo is DetailedWebViewAdapterInfo { IsInstalled: false } info)
                SetError(info.UnavailableReason ?? "The native browser engine is not installed.");
        };

        if (Color.TryParse(project.Background, out var color))
            view.Background = new SolidColorBrush(color);

        _renderer = view;
        Content = view;
        view.Source = new Uri(Path.GetFullPath(project.EntryPath));
        return Task.CompletedTask;
    }

    private void InvokeWebScript(NativeWebView view, string script)
    {
        try
        {
            view.InvokeScript(script);
        }
        catch (InvalidOperationException exception)
        {
            SetError($"HTML wallpaper script could not run: {exception.Message}");
        }
    }

    internal class WallpaperImageRenderer : Control
    {
        private readonly Bitmap _bitmap;
        private readonly string _fit;
        private readonly string _alignment;

        public WallpaperImageRenderer(Bitmap bitmap, string fit, string alignment)
        {
            _bitmap = bitmap;
            _fit = fit;
            _alignment = alignment;
            ClipToBounds = true;
        }

        public override void Render(DrawingContext context)
        {
            var bounds = new Rect(Bounds.Size);
            if (bounds.Width <= 0 || bounds.Height <= 0 || _bitmap.Size.Width <= 0 || _bitmap.Size.Height <= 0)
                return;

            using (context.PushClip(bounds))
            {
                if (_fit == "tile")
                {
                    DrawTiled(context, bounds);
                    return;
                }

                var scaleX = bounds.Width / _bitmap.Size.Width;
                var scaleY = bounds.Height / _bitmap.Size.Height;
                var scale = _fit switch
                {
                    "stretch" => 0,
                    "fit" => Math.Min(scaleX, scaleY),
                    "center" => 1,
                    _ => Math.Max(scaleX, scaleY)
                };
                var width = _fit == "stretch" ? bounds.Width : _bitmap.Size.Width * scale;
                var height = _fit == "stretch" ? bounds.Height : _bitmap.Size.Height * scale;
                context.DrawImage(
                    _bitmap,
                    new Rect(0, 0, _bitmap.Size.Width, _bitmap.Size.Height),
                    new Rect(AlignX(bounds, width), AlignY(bounds, height), width, height));
            }
        }

        private void DrawTiled(DrawingContext context, Rect bounds)
        {
            var width = _bitmap.Size.Width;
            var height = _bitmap.Size.Height;
            var startX = AlignX(bounds, width);
            var startY = AlignY(bounds, height);
            startX -= Math.Ceiling(startX / width) * width;
            startY -= Math.Ceiling(startY / height) * height;
            var source = new Rect(0, 0, width, height);
            for (var y = startY; y < bounds.Bottom; y += height)
            for (var x = startX; x < bounds.Right; x += width)
                context.DrawImage(_bitmap, source, new Rect(x, y, width, height));
        }

        private double AlignX(Rect bounds, double contentWidth)
        {
            if (HasAlignment("left"))
                return bounds.Left;
            if (HasAlignment("right"))
                return bounds.Right - contentWidth;
            return bounds.Left + (bounds.Width - contentWidth) / 2;
        }

        private double AlignY(Rect bounds, double contentHeight)
        {
            if (HasAlignment("top"))
                return bounds.Top;
            if (HasAlignment("bottom"))
                return bounds.Bottom - contentHeight;
            return bounds.Top + (bounds.Height - contentHeight) / 2;
        }

        private bool HasAlignment(string value) =>
            _alignment.Split('-', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
                .Contains(value, StringComparer.OrdinalIgnoreCase);
    }

    private sealed class AnimatedWallpaperImageRenderer : WallpaperImageRenderer, IDisposable
    {
        private const long MaximumFrameBytes = 256L * 1024 * 1024;
        private readonly SKCodec _codec;
        private readonly SKImageInfo _imageInfo;
        private readonly DispatcherTimer _timer;
        private readonly WriteableBitmap _writeableBitmap;
        private int _frameIndex;
        private bool _disposed;

        private AnimatedWallpaperImageRenderer(
            SKCodec codec,
            WriteableBitmap bitmap,
            string fit,
            string alignment)
            : base(bitmap, fit, alignment)
        {
            _codec = codec;
            _writeableBitmap = bitmap;
            _imageInfo = new SKImageInfo(
                bitmap.PixelSize.Width,
                bitmap.PixelSize.Height,
                SKColorType.Rgba8888,
                SKAlphaType.Premul);
            _timer = new DispatcherTimer();
            _timer.Tick += AdvanceFrame;
            DecodeFrame(0);
            SetFrameDelay(0);
        }

        public static AnimatedWallpaperImageRenderer? TryCreate(string path, string fit, string alignment)
        {
            var codec = SKCodec.Create(path);
            if (codec is null)
                return null;
            if (codec.FrameCount <= 1)
            {
                codec.Dispose();
                return null;
            }

            var dimensions = codec.Info;
            var frameBytes = (long)dimensions.Width * dimensions.Height * 4;
            if (dimensions.Width <= 0 || dimensions.Height <= 0 || frameBytes > MaximumFrameBytes)
            {
                codec.Dispose();
                throw new InvalidDataException("Animated image dimensions exceed the supported memory limit.");
            }

            WriteableBitmap? bitmap = null;
            try
            {
                bitmap = new WriteableBitmap(
                    new PixelSize(dimensions.Width, dimensions.Height),
                    new Vector(96, 96),
                    PixelFormat.Rgba8888,
                    AlphaFormat.Premul);
                var renderer = new AnimatedWallpaperImageRenderer(codec, bitmap, fit, alignment);
                bitmap = null;
                return renderer;
            }
            catch
            {
                bitmap?.Dispose();
                codec.Dispose();
                throw;
            }
        }

        public void Start()
        {
            ObjectDisposedException.ThrowIf(_disposed, this);
            if (!_timer.IsEnabled)
                _timer.Start();
        }

        public void Pause() => _timer.Stop();

        public void Stop()
        {
            _timer.Stop();
            _frameIndex = 0;
            DecodeFrame(_frameIndex);
            SetFrameDelay(_frameIndex);
            InvalidateVisual();
        }

        public void Dispose()
        {
            if (_disposed)
                return;
            _disposed = true;
            _timer.Stop();
            _timer.Tick -= AdvanceFrame;
            _codec.Dispose();
            _writeableBitmap.Dispose();
        }

        private void AdvanceFrame(object? sender, EventArgs e)
        {
            _frameIndex = (_frameIndex + 1) % _codec.FrameCount;
            DecodeFrame(_frameIndex);
            SetFrameDelay(_frameIndex);
            InvalidateVisual();
        }

        private void DecodeFrame(int index)
        {
            var frameInfo = _codec.FrameInfo[index];
            using var framebuffer = _writeableBitmap.Lock();
            var result = _codec.GetPixels(
                _imageInfo,
                framebuffer.Address,
                framebuffer.RowBytes,
                new SKCodecOptions(index, frameInfo.RequiredFrame));
            if (result is not (SKCodecResult.Success or SKCodecResult.IncompleteInput))
                throw new InvalidDataException($"Could not decode GIF frame {index}: {result}.");
        }

        private void SetFrameDelay(int index)
        {
            var delay = _codec.FrameInfo[index].Duration;
            _timer.Interval = TimeSpan.FromMilliseconds(Math.Max(20, delay));
        }
    }

    private void OnVideoEnded(object? sender, EventArgs e)
    {
        if (!_loopVideos || !_started || _disposed)
            return;
        Dispatcher.UIThread.Post(() =>
        {
            if (!_started || _disposed || _mediaPlayer is null || _media is null)
                return;
            _mediaPlayer.Stop();
            _mediaPlayer.Play(_media);
        });
    }

    private void StartVideo()
    {
        if (!_started || _videoPlayStarted || _pauseRequested || _mediaPlayer is null || _media is null)
            return;
        if (_mediaPlayer.Play(_media))
            _videoPlayStarted = true;
        else
            SetError($"LibVLC could not start video: {_wallpaper.Path}");
    }

    private void SetError(string message) => PlaybackError?.Invoke(this, message);
}

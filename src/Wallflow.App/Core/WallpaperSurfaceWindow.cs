using System.Diagnostics;
using System.Runtime.InteropServices;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Platform;
using Avalonia.Threading;

namespace Wallflow.App.Core;

internal sealed class WallpaperSurfaceWindow : Window
{
    private readonly LiveWallpaperControl _wallpaper;
    private readonly string _name;
    private readonly Action<string> _report;
    private readonly int _transitionMs;
    private readonly Stopwatch _transitionClock = new();
    private readonly DispatcherTimer _transitionTimer;
    private bool _clickable;
    private PixelRect _monitorBounds;
    private PixelRect _virtualBounds;

    public WallpaperSurfaceWindow(
        Screen screen,
        MonitorInfo monitor,
        Wallpaper wallpaper,
        bool loopVideos,
        bool videoAudio,
        int transitionMs,
        bool interactiveWebWallpaper,
        Action<string> reportError,
        int maxFps = 60)
    {
        _transitionMs = Math.Max(0, transitionMs);
        _transitionTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(16) };
        _transitionTimer.Tick += (_, _) => AdvanceTransition();
        var scale = screen.Scaling <= 0 ? 1 : screen.Scaling;
        _clickable = interactiveWebWallpaper && wallpaper.WallpaperType == WallpaperKind.Web;
        _monitorBounds = screen.Bounds;
        _name = wallpaper.Name;
        _report = reportError;
        _wallpaper = new LiveWallpaperControl(wallpaper, loopVideos, videoAudio, maxFps);
        _wallpaper.PlaybackError += (_, message) => _report($"{monitor.Name}: {message}");
        _wallpaper.SourceResolutionResolved += (_, size) => WarnIfUpscaled(size);

        Title = $"Wallflow wallpaper — {monitor.Name}";
        WindowDecorations = Avalonia.Controls.WindowDecorations.None;
        ShowInTaskbar = false;
        CanResize = false;
        Topmost = false;
        Background = Avalonia.Media.Brushes.Black;
        Position = new PixelPoint(screen.Bounds.X, screen.Bounds.Y);
        Width = screen.Bounds.Width / scale;
        Height = screen.Bounds.Height / scale;
        Opacity = _transitionMs > 0 ? 0 : 1;
        Content = _wallpaper;

        Opened += async (_, _) =>
        {
            try
            {
                await _wallpaper.StartAsync();
            }
            catch (Exception exception) when (
                exception is IOException or UnauthorizedAccessException or InvalidDataException or
                    InvalidOperationException or NotSupportedException or System.Text.Json.JsonException)
            {
                reportError($"{monitor.Name}: {exception.Message}");
            }
            BeginTransition();
        };
        Closed += (_, _) =>
        {
            _transitionTimer.Stop();
            _wallpaper.Dispose();
        };
    }

    public void PauseWallpaper() => _wallpaper.Pause();
    public void ResumeWallpaper() => _wallpaper.Resume();

    /// <summary>
    /// Warns once when a video is smaller than the monitor it is stretched across,
    /// the way the desktop-tools version of Wallflow always did.
    /// </summary>
    internal static string UpscaleWarning(string name, MediaSize size, int monitorWidth, int monitorHeight)
    {
        var factor = monitorWidth / (double)size.Width;
        var text = System.Globalization.CultureInfo.InvariantCulture;
        return $"«{name}»: видео {size.Width}×{size.Height}, а монитор {monitorWidth}×{monitorHeight} — " +
               string.Create(text, $"картинка растягивается в {factor:0.0} раз, поэтому будет размытой. ") +
               "Возьмите ролик в 1080p или 4K.";
    }

    private void WarnIfUpscaled(MediaSize size)
    {
        var monitorWidth = _monitorBounds.Width;
        if (monitorWidth <= 0 || size.Width >= monitorWidth)
            return;
        _report(UpscaleWarning(_name, size, monitorWidth, _monitorBounds.Height));
    }

    public bool AttachToDesktop(PixelRect monitorBounds, PixelRect virtualBounds) =>
        SetDesktopPlacement(monitorBounds, virtualBounds);

    public void UpdateSettings(bool loopVideos, bool videoAudio, bool interactiveWebWallpaper, int maxFps)
    {
        _wallpaper.UpdateSettings(loopVideos, videoAudio, maxFps);
        if (_wallpaper.WallpaperType != WallpaperKind.Web)
            return;
        var clickable = interactiveWebWallpaper;
        if (_clickable == clickable)
            return;
        _clickable = clickable;
        if (!SetDesktopPlacement(_monitorBounds, _virtualBounds))
            throw new NotSupportedException("Could not update HTML wallpaper mouse interaction.");
    }

    private bool SetDesktopPlacement(PixelRect monitorBounds, PixelRect virtualBounds)
    {
        _monitorBounds = monitorBounds;
        _virtualBounds = virtualBounds;
        return DesktopSurfacePlatform.Attach(this, monitorBounds, virtualBounds, _clickable);
    }

    private void BeginTransition()
    {
        if (_transitionMs == 0)
            return;
        _transitionClock.Restart();
        _transitionTimer.Start();
    }

    private void AdvanceTransition()
    {
        var progress = Math.Clamp(_transitionClock.Elapsed.TotalMilliseconds / _transitionMs, 0, 1);
        Opacity = progress * progress * (3 - 2 * progress);
        if (progress < 1)
            return;
        _transitionTimer.Stop();
        _transitionClock.Stop();
    }
}

internal static class DesktopSurfacePlatform
{
    public static bool Attach(Window window, PixelRect monitorBounds, PixelRect virtualBounds, bool clickable)
    {
        var platformHandle = window.TryGetPlatformHandle();
        if (platformHandle is null)
            return false;

        if (OperatingSystem.IsWindows())
            return AttachWindows(platformHandle.Handle, monitorBounds, virtualBounds, clickable);
        if (OperatingSystem.IsLinux())
        {
            try
            {
                return AttachX11(platformHandle, monitorBounds);
            }
            catch (DllNotFoundException)
            {
                return false;
            }
            catch (EntryPointNotFoundException)
            {
                return false;
            }
        }
        if (OperatingSystem.IsMacOS())
            return AttachMacOs(platformHandle, clickable);
        return false;
    }

    private static bool AttachWindows(
        IntPtr hwnd,
        PixelRect monitorBounds,
        PixelRect virtualBounds,
        bool clickable)
    {
        var programManager = FindWindow("Progman", null);
        if (programManager == IntPtr.Zero)
            return false;

        SendMessageTimeout(programManager, 0x052C, new IntPtr(0xD), new IntPtr(0x1), 0, 1000, out _);
        var worker = FindWallpaperWorker();
        var parent = worker != IntPtr.Zero ? worker : programManager;
        SetLastError(0);
        if (SetParent(hwnd, parent) == IntPtr.Zero && Marshal.GetLastWin32Error() != 0)
            return false;

        var style = GetWindowLong(hwnd, -16).ToInt64();
        style = (style & ~0x80000000L) | 0x40000000L | 0x10000000L;
        SetWindowLong(hwnd, -16, new IntPtr(style));
        var extendedStyle = GetWindowLong(hwnd, -20).ToInt64();
        extendedStyle |= 0x00000080L | 0x08000000L;
        if (clickable)
            extendedStyle &= ~0x00000020L;
        else
            extendedStyle |= 0x00000020L;
        SetWindowLong(hwnd, -20, new IntPtr(extendedStyle));

        const uint flags = 0x0010 | 0x0040 | 0x0200;
        return SetWindowPos(
            hwnd,
            new IntPtr(1),
            monitorBounds.X - virtualBounds.X,
            monitorBounds.Y - virtualBounds.Y,
            monitorBounds.Width,
            monitorBounds.Height,
            flags);
    }

    private static IntPtr FindWallpaperWorker()
    {
        var worker = IntPtr.Zero;
        EnumWindows((topLevel, _) =>
        {
            if (FindWindowEx(topLevel, IntPtr.Zero, "SHELLDLL_DefView", null) == IntPtr.Zero)
                return true;
            worker = FindWindowEx(IntPtr.Zero, topLevel, "WorkerW", null);
            return worker == IntPtr.Zero;
        }, IntPtr.Zero);
        return worker;
    }

    private static bool AttachX11(IPlatformHandle handle, PixelRect bounds)
    {
        if (!string.Equals(handle.HandleDescriptor, "XID", StringComparison.OrdinalIgnoreCase))
            return false;
        var display = XOpenDisplay(IntPtr.Zero);
        if (display == IntPtr.Zero)
            return false;

        try
        {
            var window = unchecked((nuint)handle.Handle.ToInt64());
            SetAtomProperty(display, window, "_NET_WM_WINDOW_TYPE", "_NET_WM_WINDOW_TYPE_DESKTOP");
            SetAtomProperty(display, window, "_NET_WM_STATE",
                "_NET_WM_STATE_SKIP_PAGER", "_NET_WM_STATE_SKIP_TASKBAR", "_NET_WM_STATE_STICKY");
            XMoveResizeWindow(display, window, bounds.X, bounds.Y, (uint)bounds.Width, (uint)bounds.Height);
            XFlush(display);
            XSync(display, 0);
            return true;
        }
        finally
        {
            XCloseDisplay(display);
        }
    }

    private static void SetAtomProperty(IntPtr display, nuint window, string propertyName, params string[] atomNames)
    {
        var property = XInternAtom(display, propertyName, false);
        var atoms = atomNames.Select(name => XInternAtom(display, name, false)).ToArray();
        var bytes = IntPtr.Size * atoms.Length;
        var values = Marshal.AllocHGlobal(bytes);
        try
        {
            for (var index = 0; index < atoms.Length; index++)
                Marshal.WriteIntPtr(values, index * IntPtr.Size, new IntPtr(atoms[index].ToInt64()));
            XChangeProperty(display, window, property, new IntPtr(4), 32, 0, values, atoms.Length);
        }
        finally
        {
            Marshal.FreeHGlobal(values);
        }
    }

    private static bool AttachMacOs(IPlatformHandle handle, bool clickable)
    {
        if (!string.Equals(handle.HandleDescriptor, "NSView", StringComparison.OrdinalIgnoreCase))
            return false;

        var nativeView = handle.Handle;
        var window = ObjcSendMessage(nativeView, Selector("window"));
        if (window == IntPtr.Zero)
            return false;

        var desktopLevel = CGWindowLevelForKey(2);
        ObjcSendMessage(window, Selector("setLevel:"), desktopLevel);
        ObjcSendMessage(window, Selector("setIgnoresMouseEvents:"), (byte)(clickable ? 0 : 1));
        ObjcSendMessage(window, Selector("setCollectionBehavior:"), (nint)(1 | 16 | 64));
        return true;
    }

    private static IntPtr Selector(string name) => SelRegisterName(name);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr FindWindow(string? className, string? windowName);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr FindWindowEx(IntPtr parent, IntPtr childAfter, string className, string? windowName);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr parameter);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr SendMessageTimeout(
        IntPtr hwnd, uint message, IntPtr wParam, IntPtr lParam, uint flags, uint timeout, out IntPtr result);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr SetParent(IntPtr child, IntPtr newParent);

    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW", SetLastError = true)]
    private static extern IntPtr GetWindowLongPtr64(IntPtr hwnd, int index);

    [DllImport("user32.dll", EntryPoint = "GetWindowLongW", SetLastError = true)]
    private static extern int GetWindowLong32(IntPtr hwnd, int index);

    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW", SetLastError = true)]
    private static extern IntPtr SetWindowLongPtr64(IntPtr hwnd, int index, IntPtr value);

    [DllImport("user32.dll", EntryPoint = "SetWindowLongW", SetLastError = true)]
    private static extern int SetWindowLong32(IntPtr hwnd, int index, int value);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetWindowPos(
        IntPtr hwnd, IntPtr insertAfter, int x, int y, int width, int height, uint flags);

    [DllImport("kernel32.dll", EntryPoint = "SetLastError")]
    private static extern void SetLastError(uint errorCode);

    [DllImport("libX11.so.6")]
    private static extern IntPtr XOpenDisplay(IntPtr displayName);

    [DllImport("libX11.so.6")]
    private static extern IntPtr XInternAtom(IntPtr display, string atomName, [MarshalAs(UnmanagedType.Bool)] bool onlyIfExists);

    [DllImport("libX11.so.6")]
    private static extern int XChangeProperty(
        IntPtr display, nuint window, IntPtr property, IntPtr type, int format, int mode, IntPtr data, int elementCount);

    [DllImport("libX11.so.6")]
    private static extern int XMoveResizeWindow(IntPtr display, nuint window, int x, int y, uint width, uint height);

    [DllImport("libX11.so.6")]
    private static extern int XFlush(IntPtr display);

    [DllImport("libX11.so.6")]
    private static extern int XSync(IntPtr display, int discard);

    [DllImport("libX11.so.6")]
    private static extern int XCloseDisplay(IntPtr display);

    [DllImport("/usr/lib/libobjc.A.dylib", EntryPoint = "objc_msgSend")]
    private static extern IntPtr ObjcSendMessage(IntPtr receiver, IntPtr selector);

    [DllImport("/usr/lib/libobjc.A.dylib", EntryPoint = "objc_msgSend")]
    private static extern void ObjcSendMessage(IntPtr receiver, IntPtr selector, nint value);

    [DllImport("/usr/lib/libobjc.A.dylib", EntryPoint = "objc_msgSend")]
    private static extern void ObjcSendMessage(IntPtr receiver, IntPtr selector, byte value);

    [DllImport("/usr/lib/libobjc.A.dylib", EntryPoint = "sel_registerName", CharSet = CharSet.Ansi)]
    private static extern IntPtr SelRegisterName(string name);

    [DllImport("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")]
    private static extern int CGWindowLevelForKey(int key);

    private static IntPtr GetWindowLong(IntPtr hwnd, int index) =>
        IntPtr.Size == 8 ? GetWindowLongPtr64(hwnd, index) : new IntPtr(GetWindowLong32(hwnd, index));

    private static IntPtr SetWindowLong(IntPtr hwnd, int index, IntPtr value) =>
        IntPtr.Size == 8
            ? SetWindowLongPtr64(hwnd, index, value)
            : new IntPtr(SetWindowLong32(hwnd, index, value.ToInt32()));

    private delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr parameter);
}

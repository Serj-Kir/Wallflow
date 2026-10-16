using System.Runtime.InteropServices;
using Avalonia.Controls;
using Avalonia.Platform;
using Avalonia.Threading;

namespace Wallflow.App.Core;

public sealed class GlobalHotkeyManager : IDisposable
{
    private const int ModAlt = 0x0001;
    private const int ModControl = 0x0002;
    private const int ModNoRepeat = 0x4000;
    private const int WindowsHotkeyMessage = 0x0312;
    private const int X11ControlMask = 1 << 2;
    private const int X11AltMask = 1 << 3;
    private const int X11GrabModeAsync = 1;
    private const int X11KeyPress = 2;
    private const int X11ModifierMask = X11ControlMask | X11AltMask;
    private static readonly nuint SubclassId = 0x57464C;

    private readonly Window _window;
    private readonly Action<string> _reportStatus;
    private readonly Dictionary<int, Action> _actions;
    private readonly SubclassProcedure _subclassProcedure;
    private readonly CancellationTokenSource _x11Cancellation = new();
    private Thread? _x11Thread;
    private IntPtr _windowHandle;
    private readonly HashSet<int> _registeredWindowsHotkeys = [];
    private bool _subclassInstalled;
    private bool _disposed;

    public GlobalHotkeyManager(
        Window window,
        Action pause,
        Action next,
        Action previous,
        Action show,
        Action<string> reportStatus)
    {
        _window = window;
        _reportStatus = reportStatus;
        _actions = new Dictionary<int, Action>
        {
            [1] = pause,
            [2] = next,
            [3] = previous,
            [4] = show
        };
        _subclassProcedure = HandleWindowsMessage;
        _window.Opened += OnOpened;
        _window.Closed += (_, _) => Dispose();
    }

    public void Dispose()
    {
        if (_disposed)
            return;
        _disposed = true;
        _window.Opened -= OnOpened;
        _x11Cancellation.Cancel();

        if (_windowHandle != IntPtr.Zero)
        {
            foreach (var id in _registeredWindowsHotkeys)
                UnregisterHotKey(_windowHandle, id);
            _registeredWindowsHotkeys.Clear();
            if (_subclassInstalled)
                RemoveWindowSubclass(_windowHandle, _subclassProcedure, SubclassId);
            _subclassInstalled = false;
            _windowHandle = IntPtr.Zero;
        }

        if (_x11Thread is { IsAlive: true } thread && thread != Thread.CurrentThread)
            thread.Join(TimeSpan.FromSeconds(1));
        _x11Cancellation.Dispose();
    }

    private void OnOpened(object? sender, EventArgs e)
    {
        if (OperatingSystem.IsWindows())
        {
            RegisterWindowsHotkeys();
            return;
        }
        if (OperatingSystem.IsLinux() && !string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("DISPLAY")))
        {
            _x11Thread = new Thread(RegisterX11Hotkeys) { IsBackground = true, Name = "Wallflow X11 hotkeys" };
            _x11Thread.Start();
            return;
        }
        _reportStatus("Глобальные клавиши недоступны в этой графической сессии.");
    }

    private void RegisterWindowsHotkeys()
    {
        _windowHandle = _window.TryGetPlatformHandle()?.Handle ?? IntPtr.Zero;
        if (_windowHandle == IntPtr.Zero)
        {
            _reportStatus("Не удалось получить окно для регистрации горячих клавиш.");
            return;
        }
        _subclassInstalled = SetWindowSubclass(_windowHandle, _subclassProcedure, SubclassId, IntPtr.Zero);
        if (!_subclassInstalled)
        {
            _reportStatus("Не удалось подключить обработчик глобальных горячих клавиш.");
            return;
        }

        var bindings = new (int Id, uint Modifiers, uint Key)[]
        {
            (1, ModControl | ModAlt | ModNoRepeat, 0x50),
            (2, ModControl | ModAlt | ModNoRepeat, 0x27),
            (3, ModControl | ModAlt | ModNoRepeat, 0x25),
            (4, ModControl | ModAlt | ModNoRepeat, 0x57)
        };
        foreach (var binding in bindings)
        {
            if (RegisterHotKey(_windowHandle, binding.Id, binding.Modifiers, binding.Key))
                _registeredWindowsHotkeys.Add(binding.Id);
            else
                _reportStatus($"Горячая клавиша действия {binding.Id} уже занята другим приложением.");
        }
    }

    private IntPtr HandleWindowsMessage(
        IntPtr hwnd,
        uint message,
        IntPtr wParam,
        IntPtr lParam,
        nuint subclassId,
        IntPtr referenceData)
    {
        if (message == WindowsHotkeyMessage && _actions.TryGetValue(wParam.ToInt32(), out var action))
        {
            Dispatcher.UIThread.Post(action);
            return IntPtr.Zero;
        }
        return DefSubclassProc(hwnd, message, wParam, lParam);
    }

    private void RegisterX11Hotkeys()
    {
        try
        {
            RegisterX11HotkeysCore();
        }
        catch (DllNotFoundException)
        {
            ReportFromWorker("Для глобальных клавиш X11 не найдена библиотека libX11.");
        }
        catch (EntryPointNotFoundException)
        {
            ReportFromWorker("В установленной версии X11 недоступен нужный API для горячих клавиш.");
        }
    }

    private void RegisterX11HotkeysCore()
    {
        var display = XOpenDisplay(IntPtr.Zero);
        if (display == IntPtr.Zero)
        {
            ReportFromWorker("Не удалось подключиться к X11 для регистрации горячих клавиш.");
            return;
        }
        var root = XDefaultRootWindow(display);
        var bindings = new (int Id, nuint KeySymbol)[]
        {
            (1, 0x70),
            (2, 0xFF53),
            (3, 0xFF51),
            (4, 0x77)
        };
        var keycodes = new Dictionary<uint, int>();
        foreach (var (id, symbol) in bindings)
        {
            var keycode = XKeysymToKeycode(display, symbol);
            if (keycode == 0)
                continue;
            XGrabKey(display, keycode, X11ModifierMask, root, true,
                X11GrabModeAsync, X11GrabModeAsync);
            keycodes[keycode] = id;
        }
        XFlush(display);

        var eventBuffer = Marshal.AllocHGlobal(192);
        try
        {
            while (!_x11Cancellation.IsCancellationRequested)
            {
                if (XPending(display) > 0)
                {
                    XNextEvent(display, eventBuffer);
                    var keyEvent = Marshal.PtrToStructure<XKeyEvent>(eventBuffer);
                    if (keyEvent.Type == X11KeyPress &&
                        keycodes.TryGetValue(keyEvent.KeyCode, out var id) &&
                        _actions.TryGetValue(id, out var action))
                        Dispatcher.UIThread.Post(action);
                }
                else
                {
                    Thread.Sleep(8);
                }
            }
        }
        finally
        {
            Marshal.FreeHGlobal(eventBuffer);
            foreach (var keycode in keycodes.Keys)
                XUngrabKey(display, checked((int)keycode), X11ModifierMask, root);
            XFlush(display);
            XCloseDisplay(display);
        }
    }

    private void ReportFromWorker(string message) =>
        Dispatcher.UIThread.Post(() => _reportStatus(message));

    [StructLayout(LayoutKind.Sequential)]
    private struct XKeyEvent
    {
        public int Type;
        public nuint Serial;
        public int SendEvent;
        public IntPtr Display;
        public nuint Window;
        public nuint Root;
        public nuint Subwindow;
        public nuint Time;
        public int X;
        public int Y;
        public int XRoot;
        public int YRoot;
        public uint State;
        public uint KeyCode;
        public int SameScreen;
    }

    [DllImport("comctl32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetWindowSubclass(
        IntPtr hwnd, SubclassProcedure procedure, nuint subclassId, IntPtr referenceData);

    [DllImport("comctl32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool RemoveWindowSubclass(
        IntPtr hwnd, SubclassProcedure procedure, nuint subclassId);

    [DllImport("comctl32.dll")]
    private static extern IntPtr DefSubclassProc(IntPtr hwnd, uint message, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool RegisterHotKey(IntPtr hwnd, int id, uint modifiers, uint key);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool UnregisterHotKey(IntPtr hwnd, int id);

    [DllImport("libX11.so.6")]
    private static extern IntPtr XOpenDisplay(IntPtr displayName);

    [DllImport("libX11.so.6")]
    private static extern nuint XDefaultRootWindow(IntPtr display);

    [DllImport("libX11.so.6")]
    private static extern byte XKeysymToKeycode(IntPtr display, nuint keysym);

    [DllImport("libX11.so.6")]
    private static extern int XGrabKey(
        IntPtr display, int keycode, uint modifiers, nuint grabWindow,
        [MarshalAs(UnmanagedType.Bool)] bool ownerEvents, int pointerMode, int keyboardMode);

    [DllImport("libX11.so.6")]
    private static extern int XUngrabKey(IntPtr display, int keycode, uint modifiers, nuint grabWindow);

    [DllImport("libX11.so.6")]
    private static extern int XPending(IntPtr display);

    [DllImport("libX11.so.6")]
    private static extern int XNextEvent(IntPtr display, IntPtr eventReturn);

    [DllImport("libX11.so.6")]
    private static extern int XFlush(IntPtr display);

    [DllImport("libX11.so.6")]
    private static extern int XCloseDisplay(IntPtr display);

    [UnmanagedFunctionPointer(CallingConvention.Winapi)]
    private delegate IntPtr SubclassProcedure(
        IntPtr hwnd,
        uint message,
        IntPtr wParam,
        IntPtr lParam,
        nuint subclassId,
        IntPtr referenceData);
}

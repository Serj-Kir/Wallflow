using System.Diagnostics;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Platform;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Platform;

namespace Wallflow.App.Core;

public sealed class WallflowTrayService : IDisposable
{
    private readonly Application _application;
    private readonly TrayIcon _icon;
    private readonly TrayIcons _icons;
    private readonly NativeMenuItem _pauseItem;
    private bool _disposed;

    public static bool IsSupported()
    {
        var implementation = PlatformManager.CreateTrayIcon();
        if (implementation is null)
            return false;
        implementation.Dispose();
        return true;
    }

    public WallflowTrayService(
        Application application,
        Action showWindow,
        Action togglePause,
        Action next,
        Action previous,
        Action openLibrary,
        Action quit)
    {
        _application = application;
        var menu = new NativeMenu();
        AddItem(menu, "Открыть Wallflow", showWindow);
        menu.Items.Add(new NativeMenuItemSeparator());
        _pauseItem = AddItem(menu, "Пауза", togglePause);
        AddItem(menu, "Следующие обои", next);
        AddItem(menu, "Предыдущие обои", previous);
        menu.Items.Add(new NativeMenuItemSeparator());
        AddItem(menu, "Открыть папку обоев", openLibrary);
        menu.Items.Add(new NativeMenuItemSeparator());
        AddItem(menu, "Выход", quit);

        _icon = new TrayIcon
        {
            Icon = CreateIcon(),
            ToolTipText = "Wallflow",
            Menu = menu,
            IsVisible = true
        };
        _icon.Clicked += (_, _) => showWindow();
        _icons = TrayIcon.GetIcons(application) ?? new TrayIcons();
        _icons.Add(_icon);
        TrayIcon.SetIcons(application, _icons);
    }

    public void SetPaused(bool paused) =>
        _pauseItem.Header = paused ? "Продолжить" : "Пауза";

    public void SetStatus(string text) =>
        _icon.ToolTipText = string.IsNullOrWhiteSpace(text) ? "Wallflow" : $"Wallflow — {text}";

    public void Dispose()
    {
        if (_disposed)
            return;
        _disposed = true;
        _icons.Remove(_icon);
        if (_icons.Count == 0)
            TrayIcon.SetIcons(_application, null);
    }

    private static NativeMenuItem AddItem(NativeMenu menu, string title, Action action)
    {
        var item = new NativeMenuItem(title);
        item.Click += (_, _) => action();
        menu.Items.Add(item);
        return item;
    }

    private static WindowIcon CreateIcon()
    {
        using var bitmap = new RenderTargetBitmap(new PixelSize(32, 32), new Vector(96, 96));
        using (var drawing = bitmap.CreateDrawingContext())
        {
            drawing.DrawRectangle(
                new SolidColorBrush(Color.Parse("#1b1c23")),
                null,
                new Rect(0, 0, 32, 32),
                7,
                7);
            drawing.DrawRectangle(new SolidColorBrush(Color.Parse("#5b8cff")), null, new Rect(5, 6, 15, 10), 2, 2);
            drawing.DrawRectangle(new SolidColorBrush(Color.Parse("#8fb0ff")), null, new Rect(12, 15, 15, 11), 2, 2);
            drawing.DrawRectangle(new SolidColorBrush(Color.Parse("#3d63b8")), null, new Rect(5, 18, 7, 8), 2, 2);
        }

        using var png = new MemoryStream();
        bitmap.Save(png, PngBitmapEncoderOptions.Default);
        var image = png.ToArray();
        using var ico = new MemoryStream();
        using (var writer = new BinaryWriter(ico, System.Text.Encoding.UTF8, leaveOpen: true))
        {
            writer.Write((ushort)0);
            writer.Write((ushort)1);
            writer.Write((ushort)1);
            writer.Write((byte)32);
            writer.Write((byte)32);
            writer.Write((byte)0);
            writer.Write((byte)0);
            writer.Write((ushort)1);
            writer.Write((ushort)32);
            writer.Write(image.Length);
            writer.Write(22);
            writer.Write(image);
        }
        ico.Position = 0;
        return new WindowIcon(ico);
    }
}

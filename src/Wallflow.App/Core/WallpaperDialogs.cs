using Avalonia;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Layout;
using Avalonia.Media;

namespace Wallflow.App.Core;

public enum WallpaperDeleteChoice
{
    Cancel,
    RemoveFromLibrary,
    DeleteFile
}

/// <summary>
/// Small modal windows for the library: wallpaper properties and the delete
/// confirmation with the same three-way choice the desktop-tools version used.
/// </summary>
public static class WallpaperDialogs
{
    private static readonly IBrush Background = new SolidColorBrush(Color.Parse("#17181D"));
    private static readonly IBrush Muted = new SolidColorBrush(Color.Parse("#A7A9B3"));

    public static async Task ShowPropertiesAsync(
        Window owner,
        Wallpaper wallpaper,
        IReadOnlyList<MonitorInfo> monitors)
    {
        var panel = new StackPanel { Spacing = 8, Margin = new Thickness(18) };
        Add(panel, wallpaper.Name, 20, FontWeight.SemiBold);
        Add(panel, $"Тип: {wallpaper.KindLabel}", 13);
        Add(panel, $"Путь: {wallpaper.Path}", 13, muted: true, wrap: true);

        if (wallpaper.WallpaperType is WallpaperKind.Image or WallpaperKind.Video)
        {
            var size = await Task.Run(() => MediaInfo.TryGetSizeAsync(wallpaper).GetAwaiter().GetResult());
            Add(panel, size is { } resolution
                ? $"Разрешение: {MediaInfo.Describe(resolution)}"
                : "Разрешение: неизвестно", 13);
            if (size is { } measured)
            {
                var widest = monitors.Count == 0 ? null : monitors.OrderByDescending(monitor => monitor.Width).First();
                if (widest is not null && measured.Width < widest.Width)
                {
                    var factor = System.Globalization.CultureInfo.InvariantCulture;
                    Add(panel,
                        string.Create(factor,
                            $"Меньше монитора ({widest.Width}×{widest.Height}) — при показе будет растянуто в " +
                            $"{widest.Width / (double)measured.Width:0.0} раз и станет размытым"),
                        12, muted: true, wrap: true);
                }
                else if (widest is not null)
                {
                    Add(panel, $"Монитор: {widest.Width}×{widest.Height} — разрешения хватает", 12, muted: true);
                }
            }
        }

        if (File.Exists(wallpaper.Path))
            Add(panel, $"Размер файла: {Humanize.Bytes(new FileInfo(wallpaper.Path).Length)}", 13);
        if (wallpaper.Author.Length > 0)
            Add(panel, $"Автор: {wallpaper.Author}", 13);
        if (wallpaper.Tags.Count > 0)
            Add(panel, $"Теги: {string.Join(", ", wallpaper.Tags)}", 13);
        if (wallpaper.Description.Length > 0)
            Add(panel, wallpaper.Description, 13, wrap: true);
        if (wallpaper.SourceUrl.Length > 0)
            Add(panel, $"Источник: {wallpaper.SourceUrl}", 12, muted: true, wrap: true);

        await ShowAsync(owner, $"Свойства — {wallpaper.Name}", panel, [("Закрыть", true)]);
    }

    /// <summary>A generic yes/no confirmation.</summary>
    public static async Task<bool> ConfirmAsync(
        Window owner,
        string title,
        string message,
        string confirmLabel = "ОК",
        string cancelLabel = "Отмена")
    {
        var panel = new StackPanel { Spacing = 10, Margin = new Thickness(18) };
        Add(panel, message, 13, wrap: true);
        var confirmed = false;
        await ShowAsync(owner, title, panel,
        [
            (confirmLabel, false, (Action)(() => confirmed = true)),
            (cancelLabel, true, () => { })
        ]);
        return confirmed;
    }

    public static async Task<WallpaperDeleteChoice> ConfirmDeleteAsync(Window owner, Wallpaper wallpaper)
    {
        var panel = new StackPanel { Spacing = 10, Margin = new Thickness(18) };
        Add(panel, $"Удалить «{wallpaper.Name}»?", 18, FontWeight.SemiBold);
        Add(panel, "«Удалить файл» стирает файл с диска, «Только из библиотеки» убирает запись.",
            13, muted: true, wrap: true);

        var choice = WallpaperDeleteChoice.Cancel;
        var buttons = new (string Label, bool Primary, WallpaperDeleteChoice Value)[]
        {
            ("Удалить файл", false, WallpaperDeleteChoice.DeleteFile),
            ("Только из библиотеки", false, WallpaperDeleteChoice.RemoveFromLibrary),
            ("Отмена", true, WallpaperDeleteChoice.Cancel)
        };
        await ShowAsync(owner, "Удаление обоев", panel, buttons.Select(button =>
            (button.Label, button.Primary, (Action)(() => choice = button.Value))));
        return choice;
    }

    private static async Task ShowAsync(
        Window owner,
        string title,
        Control content,
        IEnumerable<(string Label, bool Primary)> buttons) =>
        await ShowAsync(owner, title, content, buttons.Select(button =>
            (button.Label, button.Primary, (Action)(() => { }))));

    private static async Task ShowAsync(
        Window owner,
        string title,
        Control content,
        IEnumerable<(string Label, bool Primary, Action OnClick)> buttons)
    {
        var window = new Window
        {
            Title = title,
            Width = 520,
            SizeToContent = SizeToContent.Height,
            CanResize = false,
            WindowStartupLocation = WindowStartupLocation.CenterOwner,
            Background = Background,
            Content = content
        };
        var actions = buttons.ToArray();
        var row = new StackPanel
        {
            Orientation = Orientation.Horizontal,
            Spacing = 8,
            HorizontalAlignment = HorizontalAlignment.Right,
            Margin = new Thickness(18, 0, 18, 18)
        };
        foreach (var (label, primary, onClick) in actions)
        {
            var button = new Button { Content = label };
            if (primary)
                button.Classes.Add("accent");
            button.Click += (_, _) =>
            {
                onClick();
                window.Close();
            };
            row.Children.Add(button);
        }

        var root = new StackPanel();
        root.Children.Add(content);
        root.Children.Add(row);
        window.Content = root;
        window.KeyDown += (_, args) =>
        {
            if (args.Key == Key.Escape)
                window.Close();
        };
        await window.ShowDialog(owner);
    }

    private static void Add(
        StackPanel panel,
        string text,
        double fontSize,
        FontWeight? weight = null,
        bool muted = false,
        bool wrap = false)
    {
        panel.Children.Add(new TextBlock
        {
            Text = text,
            FontSize = fontSize,
            FontWeight = weight ?? FontWeight.Normal,
            Foreground = muted ? Muted : Brushes.White,
            TextWrapping = wrap ? TextWrapping.Wrap : TextWrapping.NoWrap
        });
    }
}

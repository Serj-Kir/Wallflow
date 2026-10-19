using System.ComponentModel;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Serialization;
using Avalonia.Media.Imaging;

namespace Wallflow.App.Core;

public enum WallpaperKind
{
    Image,
    Video,
    Web,
    Scene
}

public sealed class Wallpaper : INotifyPropertyChanged
{
    private Bitmap? _thumbnail;

    public required string Id { get; init; }
    public required string Path { get; init; }
    public string Kind { get; set; } = "image";
    public string Name { get; set; } = "";
    public string Author { get; set; } = "";
    public string Description { get; set; } = "";
    public List<string> Tags { get; set; } = [];
    public string SourceUrl { get; set; } = "";
    public double AddedAt { get; set; }
    public bool Favourite { get; set; }
    [JsonIgnore]
    public string DisplayFit { get; set; } = "fill";
    [JsonIgnore]
    public string DisplayAlignment { get; set; } = "center";

    /// <summary>Populated lazily by <see cref="ThumbnailCache"/> for the library list.</summary>
    [JsonIgnore]
    public Bitmap? Thumbnail
    {
        get => _thumbnail;
        set
        {
            if (ReferenceEquals(_thumbnail, value))
                return;
            _thumbnail = value;
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(Thumbnail)));
        }
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    public WallpaperKind WallpaperType => Kind switch
    {
        "video" => WallpaperKind.Video,
        "web" => WallpaperKind.Web,
        "scene" => WallpaperKind.Scene,
        _ => WallpaperKind.Image
    };

    [JsonIgnore]
    public string KindLabel => WallpaperType switch
    {
        WallpaperKind.Video => "Видео",
        WallpaperKind.Web => "HTML",
        WallpaperKind.Scene => "Сцена",
        _ => "Фото"
    };

    public static Wallpaper FromPath(string path)
    {
        var fullPath = System.IO.Path.GetFullPath(path);
        var extension = System.IO.Path.GetExtension(fullPath).ToLowerInvariant();
        var kind = Directory.Exists(fullPath) && File.Exists(System.IO.Path.Combine(fullPath, "index.html"))
            ? "web"
            : extension switch
        {
            ".html" => "web",
            ".scene" => "scene",
            ".mp4" or ".webm" or ".mkv" or ".avi" or ".mov" or ".m4v" or ".wmv" or ".flv" or ".mpg" or ".mpeg" => "video",
            _ => "image"
        };

        return new Wallpaper
        {
            Id = IdForPath(fullPath),
            Path = fullPath,
            Kind = kind,
            Name = Directory.Exists(fullPath)
                ? System.IO.Path.GetFileName(fullPath)
                : System.IO.Path.GetFileNameWithoutExtension(fullPath),
            AddedAt = File.Exists(fullPath)
                ? new DateTimeOffset(File.GetLastWriteTimeUtc(fullPath)).ToUnixTimeSeconds()
                : DateTimeOffset.UtcNow.ToUnixTimeSeconds()
        };
    }

    public static string IdForPath(string path)
    {
        var normalized = System.IO.Path.GetFullPath(path).ToLowerInvariant();
        return Convert.ToHexString(SHA1.HashData(Encoding.UTF8.GetBytes(normalized)))[..12].ToLowerInvariant();
    }

    public Wallpaper ForDisplay(string fit, string alignment) => new()
    {
        Id = Id,
        Path = Path,
        Kind = Kind,
        Name = Name,
        Author = Author,
        Description = Description,
        Tags = Tags,
        SourceUrl = SourceUrl,
        AddedAt = AddedAt,
        Favourite = Favourite,
        DisplayFit = fit,
        DisplayAlignment = alignment
    };
}

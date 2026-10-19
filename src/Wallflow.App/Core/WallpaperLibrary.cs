using System.Text.Json;
using System.Text.Json.Serialization;
using Wallflow.App.Core.Sources;

namespace Wallflow.App.Core;

public sealed class WallpaperLibrary
{
    private static readonly HashSet<string> SupportedExtensions = new(StringComparer.OrdinalIgnoreCase)
    {
        ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tif", ".tiff", ".avif",
        ".mp4", ".webm", ".mkv", ".avi", ".mov", ".m4v", ".wmv", ".flv", ".mpg", ".mpeg",
        ".scene", ".html", ".zip"
    };

    private static readonly HashSet<string> SkippedDirectories = new(StringComparer.OrdinalIgnoreCase)
    {
        ".git", ".svn", "node_modules", "__pycache__", ".cache", "$RECYCLE.BIN",
        "System Volume Information", ".thumbnails"
    };

    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        WriteIndented = true,
        Converters = { new JsonStringEnumConverter(JsonNamingPolicy.SnakeCaseLower) }
    };

    private readonly Dictionary<string, Wallpaper> _items = new(StringComparer.Ordinal);
    private readonly List<string> _directories = [];
    private readonly WallflowConfig _config;

    public static string[] SupportedPatterns =>
    [
        "*.jpg", "*.jpeg", "*.png", "*.bmp", "*.gif", "*.webp", "*.tif", "*.tiff", "*.avif",
        "*.mp4", "*.webm", "*.mkv", "*.avi", "*.mov", "*.m4v", "*.wmv", "*.flv", "*.mpg", "*.mpeg",
        "*.scene", "*.html", "*.zip"
    ];

    public WallpaperLibrary(WallflowConfig config)
    {
        _config = config;
        Load();
        SetDirectories(config.LibraryDirectories);
    }

    public Wallpaper? Get(string id) =>
        _items.TryGetValue(id, out var wallpaper) &&
        (File.Exists(wallpaper.Path) || Directory.Exists(wallpaper.Path))
            ? wallpaper
            : null;

    public Wallpaper? ByPath(string path) =>
        _items.TryGetValue(Wallpaper.IdForPath(path), out var wallpaper)
            ? wallpaper
            : null;

    public bool Remove(string id, bool deleteFile = false)
    {
        if (!_items.Remove(id, out var item))
            return false;
        if (deleteFile)
        {
            if (Directory.Exists(item.Path))
                Directory.Delete(item.Path, recursive: true);
            else if (File.Exists(item.Path))
                File.Delete(item.Path);
        }
        Save();
        return true;
    }

    public bool Update(
        string id,
        string? name = null,
        string? author = null,
        string? description = null,
        IEnumerable<string>? tags = null)
    {
        if (!_items.TryGetValue(id, out var item))
            return false;
        if (name is not null)
            item.Name = name;
        if (author is not null)
            item.Author = author;
        if (description is not null)
            item.Description = description;
        if (tags is not null)
            item.Tags = tags.Distinct(StringComparer.Ordinal).ToList();
        Save();
        return true;
    }

    public int Scan()
    {
        SetDirectories(_config.LibraryDirectories);
        var found = 0;
        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (var directory in _directories)
        {
            if (!Directory.Exists(directory))
                continue;

            foreach (var file in EnumerateFiles(directory))
            {
                var id = Wallpaper.IdForPath(file);
                seen.Add(id);
                found++;

                var lastWrite = new DateTimeOffset(File.GetLastWriteTimeUtc(file)).ToUnixTimeSeconds();
                if (_items.TryGetValue(id, out var old) && old.AddedAt >= lastWrite)
                    continue;

                var wallpaper = Wallpaper.FromPath(file);
                if (old is not null)
                {
                    wallpaper.Favourite = old.Favourite;
                    wallpaper.Tags = old.Tags;
                    wallpaper.Description = old.Description;
                    wallpaper.Author = old.Author;
                    wallpaper.SourceUrl = old.SourceUrl;
                    wallpaper.AddedAt = old.AddedAt;
                }
                _items[id] = wallpaper;
            }
        }

        foreach (var staleId in _items.Where(pair => !seen.Contains(pair.Key) && !File.Exists(pair.Value.Path))
                     .Select(pair => pair.Key).ToArray())
            _items.Remove(staleId);

        Save();
        return found;
    }

    public int AddFiles(IEnumerable<string> paths)
    {
        var targetDirectory = WallflowPaths.WallpaperDirectory;
        Directory.CreateDirectory(targetDirectory);
        var added = 0;
        foreach (var source in paths)
        {
            if (!File.Exists(source))
                continue;

            var filename = Path.GetFileName(source);
            var target = Path.Combine(targetDirectory, filename);
            var suffix = Path.GetExtension(filename);
            var stem = Path.GetFileNameWithoutExtension(filename);
            for (var duplicate = 1; File.Exists(target); duplicate++)
                target = Path.Combine(targetDirectory, $"{stem} ({duplicate}){suffix}");

            File.Copy(source, target);
            var wallpaper = Wallpaper.FromPath(target);
            _items[wallpaper.Id] = wallpaper;
            added++;
        }

        if (added > 0)
            Save();
        return added;
    }

    public Wallpaper AddDownloaded(SearchResult result, string downloadedPath)
    {
        if (!File.Exists(downloadedPath))
            throw new FileNotFoundException("Downloaded wallpaper was not found.", downloadedPath);

        var directory = WallflowPaths.WallpaperDirectory;
        Directory.CreateDirectory(directory);
        var target = GetUniquePath(Path.Combine(directory, WallpaperSources.SuggestFilename(result)));
        if (!string.Equals(Path.GetFullPath(downloadedPath), Path.GetFullPath(target),
                StringComparison.OrdinalIgnoreCase))
            File.Copy(downloadedPath, target);
        var wallpaper = WallpaperSources.ResultToWallpaper(result, target);
        _items[wallpaper.Id] = wallpaper;
        Save();
        return wallpaper;
    }

    public IReadOnlyList<Wallpaper> Search(string search)
        => Filter(search);

    public IReadOnlyList<Wallpaper> Filter(
        string search = "",
        IEnumerable<WallpaperKind>? kinds = null,
        bool favouritesOnly = false,
        IEnumerable<string>? tags = null,
        string folder = "")
    {
        var needle = search.Trim();
        var kindSet = kinds?.ToHashSet();
        var tagSet = tags?.ToHashSet(StringComparer.Ordinal);
        var normalizedFolder = string.IsNullOrWhiteSpace(folder) ? "" : Path.GetFullPath(folder);
        return _items.Values
            .Where(item => File.Exists(item.Path) || Directory.Exists(item.Path))
            .Where(item => !favouritesOnly || item.Favourite)
            .Where(item => kindSet is null || kindSet.Count == 0 || kindSet.Contains(item.WallpaperType))
            .Where(item => tagSet is null || tagSet.Count == 0 || item.Tags.Any(tag => tagSet.Contains(tag)))
            .Where(item => normalizedFolder.Length == 0 ||
                string.Equals(Path.GetDirectoryName(item.Path), normalizedFolder,
                    OperatingSystem.IsWindows() ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal))
            .Where(item => needle.Length == 0 ||
                item.Name.Contains(needle, StringComparison.OrdinalIgnoreCase) ||
                item.Path.Contains(needle, StringComparison.OrdinalIgnoreCase) ||
                item.Author.Contains(needle, StringComparison.OrdinalIgnoreCase) ||
                item.Description.Contains(needle, StringComparison.OrdinalIgnoreCase) ||
                item.Tags.Any(tag => tag.Contains(needle, StringComparison.OrdinalIgnoreCase)))
            .OrderByDescending(item => item.Favourite)
            .ThenBy(item => item.Name, StringComparer.OrdinalIgnoreCase)
            .ToArray();
    }

    /// <summary>
    /// All tags currently used by the wallpapers in the library.
    /// </summary>
    public IReadOnlyList<string> Tags()
    {
        return _items.Values
            .Where(item => File.Exists(item.Path) || Directory.Exists(item.Path))
            .SelectMany(item => item.Tags)
            .Distinct(StringComparer.Ordinal)
            .OrderBy(tag => tag, StringComparer.Ordinal)
            .ToArray();
    }

    public int ImportDirectory(string sourceDirectory, bool move = false)
    {
        if (!Directory.Exists(sourceDirectory))
            throw new DirectoryNotFoundException($"Import folder does not exist: {sourceDirectory}");

        var root = Path.GetFullPath(sourceDirectory);
        var added = 0;
        foreach (var source in EnumerateFiles(root))
        {
            var relativeParent = Path.GetRelativePath(root, Path.GetDirectoryName(source)!);
            var safeSegments = relativeParent == "."
                ? []
                : relativeParent.Split(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
                    .Take(2).Select(SafeFolderName).ToArray();
            var targetDirectory = safeSegments.Length == 0
                ? WallflowPaths.WallpaperDirectory
                : Path.Combine([WallflowPaths.WallpaperDirectory, .. safeSegments]);
            Directory.CreateDirectory(targetDirectory);

            var filename = Path.GetFileName(source);
            var target = GetUniquePath(Path.Combine(targetDirectory, filename));
            if (Directory.Exists(source))
            {
                CopyDirectory(source, target);
                if (move)
                    Directory.Delete(source, recursive: true);
            }
            else if (move)
                File.Move(source, target);
            else
                File.Copy(source, target);

            var wallpaper = Wallpaper.FromPath(target);
            _items[wallpaper.Id] = wallpaper;
            added++;
        }

        if (added > 0)
            Save();
        return added;
    }

    public void SetFavourite(string id, bool favourite)
    {
        if (!_items.TryGetValue(id, out var item))
            throw new KeyNotFoundException($"Wallpaper not found in library: {id}");

        item.Favourite = favourite;
        Save();
    }

    private void SetDirectories(IEnumerable<string> extraDirectories)
    {
        _directories.Clear();
        _directories.Add(WallflowPaths.WallpaperDirectory);
        var comparer = OperatingSystem.IsWindows() ? StringComparer.OrdinalIgnoreCase : StringComparer.Ordinal;
        var unique = new HashSet<string>(_directories, comparer);
        foreach (var directory in extraDirectories)
        {
            if (!Directory.Exists(directory))
                continue;
            var fullPath = Path.GetFullPath(directory);
            if (unique.Add(fullPath))
                _directories.Add(fullPath);
        }
    }

    private static IEnumerable<string> EnumerateFiles(string root)
    {
        var pending = new Stack<string>();
        pending.Push(root);
        while (pending.Count > 0)
        {
            var directory = pending.Pop();
            foreach (var subdirectory in Directory.EnumerateDirectories(directory))
            {
                var name = Path.GetFileName(subdirectory);
                if (!name.StartsWith('.') && !SkippedDirectories.Contains(name))
                    pending.Push(subdirectory);
            }

            foreach (var file in Directory.EnumerateFiles(directory))
            {
                if (Path.GetFileName(file).StartsWith('.'))
                    continue;
                var extension = Path.GetExtension(file);
                if (SupportedExtensions.Contains(extension))
                {
                    if (string.Equals(Path.GetFileName(file), "index.html", StringComparison.OrdinalIgnoreCase))
                        yield return Path.GetFullPath(Path.GetDirectoryName(file)!);
                    else
                        yield return Path.GetFullPath(file);
                }
            }
        }
    }

    private static string GetUniquePath(string path)
    {
        if (!File.Exists(path))
            return path;

        var directory = Path.GetDirectoryName(path)!;
        var extension = Path.GetExtension(path);
        var stem = Path.GetFileNameWithoutExtension(path);
        for (var duplicate = 1; ; duplicate++)
        {
            var candidate = Path.Combine(directory, $"{stem} ({duplicate}){extension}");
            if (!File.Exists(candidate))
                return candidate;
        }
    }

    private static string SafeFolderName(string name)
    {
        var invalid = Path.GetInvalidFileNameChars();
        var cleaned = new string(name.Select(character => invalid.Contains(character) ? '_' : character).ToArray())
            .Trim(' ', '.');
        return string.IsNullOrWhiteSpace(cleaned) ? "folder" : cleaned;
    }

    private static void CopyDirectory(string source, string destination)
    {
        Directory.CreateDirectory(destination);
        foreach (var file in Directory.EnumerateFiles(source))
            File.Copy(file, Path.Combine(destination, Path.GetFileName(file)));
        foreach (var child in Directory.EnumerateDirectories(source))
            CopyDirectory(child, Path.Combine(destination, Path.GetFileName(child)));
    }

    private void Load()
    {
        var indexPath = WallflowPaths.LibraryFile;
        if (!File.Exists(indexPath))
            return;

        var root = JsonNodeCompat.Parse(File.ReadAllText(indexPath));
        if (root is not System.Text.Json.Nodes.JsonObject obj ||
            obj["items"] is not System.Text.Json.Nodes.JsonArray entries)
            throw new InvalidDataException($"Library index has an invalid format: {indexPath}");

        foreach (var entry in entries)
        {
            if (entry is null)
                continue;
            try
            {
                var wallpaper = entry.Deserialize<Wallpaper>(JsonOptions);
                if (wallpaper is not null && !string.IsNullOrWhiteSpace(wallpaper.Id) &&
                    !string.IsNullOrWhiteSpace(wallpaper.Path))
                    _items[wallpaper.Id] = wallpaper;
            }
            catch (JsonException)
            {
                // An invalid entry should not prevent other library entries from loading.
            }
        }
    }

    private void Save()
    {
        Directory.CreateDirectory(WallflowPaths.DataDirectory);
        var payload = new
        {
            version = 1,
            saved_at = DateTimeOffset.UtcNow.ToUnixTimeSeconds(),
            items = _items.Values
        };
        var temporaryPath = WallflowPaths.LibraryFile + ".tmp";
        File.WriteAllText(temporaryPath, JsonSerializer.Serialize(payload, JsonOptions));
        File.Move(temporaryPath, WallflowPaths.LibraryFile, overwrite: true);
    }

    private static class JsonNodeCompat
    {
        public static System.Text.Json.Nodes.JsonNode? Parse(string json) =>
            System.Text.Json.Nodes.JsonNode.Parse(json);
    }
}

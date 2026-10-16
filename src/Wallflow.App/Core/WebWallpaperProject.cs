using System.Text.Json;

namespace Wallflow.App.Core;

public sealed class WebWallpaperProject
{
    private WebWallpaperProject(string root, string? entryPath = null)
    {
        Root = root;
        EntryPath = entryPath ?? Path.Combine(root, "index.html");
        Title = Path.GetFileName(root);
    }

    public string Root { get; }
    public string EntryPath { get; }
    public string Title { get; private set; }
    public string Author { get; private set; } = "";
    public string Description { get; private set; } = "";
    public string Background { get; private set; } = "#000000";
    public string? PreviewPath { get; private set; }
    public string? MetadataError { get; private set; }
    public bool IsValid => File.Exists(EntryPath);

    public static WebWallpaperProject Load(string path)
    {
        var fullPath = Path.GetFullPath(path);
        var root = Directory.Exists(fullPath)
            ? fullPath
            : Path.GetDirectoryName(fullPath) ?? fullPath;
        var entryPath = Directory.Exists(fullPath) ||
                        string.Equals(Path.GetFileName(fullPath), "index.html", StringComparison.OrdinalIgnoreCase)
            ? Path.Combine(root, "index.html")
            : fullPath;
        var project = new WebWallpaperProject(root, entryPath);
        if (!project.IsValid)
            return project;

        var metadataPath = Path.Combine(root, "project.json");
        if (!File.Exists(metadataPath))
            metadataPath = Path.Combine(root, "Project.json");
        if (!File.Exists(metadataPath))
            return project;

        JsonDocument document;
        try
        {
            document = JsonDocument.Parse(File.ReadAllText(metadataPath));
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or JsonException)
        {
            project.MetadataError = $"Could not read HTML wallpaper metadata '{metadataPath}': {exception.Message}";
            return project;
        }

        using (document)
        {
            if (document.RootElement.ValueKind != JsonValueKind.Object)
            {
                project.MetadataError = $"HTML wallpaper metadata must be a JSON object: {metadataPath}";
                return project;
            }

            var metadata = document.RootElement;
            project.Title = GetString(metadata, "title", "name") ?? project.Title;
            project.Author = GetString(metadata, "author", "creator") ?? "";
            project.Description = GetString(metadata, "description") ?? "";
            project.Background = GetString(metadata, "background", "backgroundColor") ?? project.Background;
            var preview = GetString(metadata, "preview", "thumbnail");
            if (!string.IsNullOrWhiteSpace(preview))
            {
                var previewPath = Path.GetFullPath(Path.Combine(root, preview));
                if (File.Exists(previewPath))
                    project.PreviewPath = previewPath;
            }
        }
        return project;
    }

    private static string? GetString(JsonElement element, params string[] names)
    {
        foreach (var name in names)
        {
            if (element.TryGetProperty(name, out var value) &&
                value.ValueKind == JsonValueKind.String &&
                !string.IsNullOrWhiteSpace(value.GetString()))
                return value.GetString();
        }
        return null;
    }
}

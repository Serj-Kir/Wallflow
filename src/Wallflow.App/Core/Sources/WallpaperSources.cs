using System.Globalization;
using System.Net;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace Wallflow.App.Core.Sources;

public sealed record SearchResult
{
    public required string Id { get; init; }
    public required string Url { get; init; }
    public string ThumbnailUrl { get; init; } = "";
    public string Title { get; init; } = "";
    public string Author { get; init; } = "";
    public string SourceUrl { get; init; } = "";
    public int Width { get; init; }
    public int Height { get; init; }
    public string Provider { get; init; } = "";
    public string License { get; init; } = "";

    public double Aspect => Width != 0 && Height != 0 ? (double)Width / Height : 16.0;
}

public sealed class ProviderError : Exception
{
    public ProviderError(string message) : base(message) { }
    public ProviderError(string message, Exception innerException) : base(message, innerException) { }
}

public abstract class Provider
{
    public abstract string Name { get; }
    public abstract string Label { get; }
    public abstract string Homepage { get; }
    public abstract string Description { get; }
    public virtual bool KeyRequired => false;
    public virtual string KeyHint => "";
    public virtual string EnvironmentVariable => "";
    public string ApiKey { get; }
    public bool Enabled => !KeyRequired || ApiKey.Length > 0;

    private readonly HttpClient _httpClient;
    private readonly TimeSpan _timeout;

    protected Provider(string apiKey = "", HttpClient? httpClient = null, TimeSpan? timeout = null)
    {
        var environmentKey = EnvironmentVariable.Length == 0
            ? ""
            : Environment.GetEnvironmentVariable(EnvironmentVariable) ?? "";
        ApiKey = (string.IsNullOrEmpty(apiKey) ? environmentKey : apiKey).Trim();
        _httpClient = httpClient ?? SourcesHttp.SharedClient;
        _timeout = timeout ?? SourcesHttp.DefaultTimeout;
        if (_timeout <= TimeSpan.Zero && _timeout != Timeout.InfiniteTimeSpan)
            throw new ArgumentOutOfRangeException(nameof(timeout), "Timeout must be positive or infinite.");
    }

    public abstract Task<IReadOnlyList<SearchResult>> SearchAsync(
        string query,
        int page = 1,
        int perPage = 30,
        CancellationToken cancellationToken = default);

    public virtual Task<IReadOnlyList<SearchResult>> DailyAsync(CancellationToken cancellationToken = default) =>
        Task.FromResult<IReadOnlyList<SearchResult>>([]);

    protected Task<JsonDocument> GetJsonAsync(
        string url,
        IReadOnlyDictionary<string, string>? headers = null,
        CancellationToken cancellationToken = default) =>
        SourcesHttp.GetJsonAsync(_httpClient, url, headers, _timeout, cancellationToken);

    protected Task<HttpResponseMessage> SendAsync(
        string url,
        IReadOnlyDictionary<string, string>? headers = null,
        CancellationToken cancellationToken = default) =>
        SourcesHttp.SendAsync(_httpClient, url, headers, _timeout, cancellationToken);
}

public sealed class BingProvider : Provider
{
    public override string Name => "bing";
    public override string Label => "Bing";
    public override string Homepage => "https://www.bing.com";
    public override string Description => "Одно качественное фото в день в нескольких разрешениях";
    private const string ArchiveUrl = "https://www.bing.com/HPImageArchive.aspx";

    public BingProvider(string apiKey = "", HttpClient? httpClient = null, TimeSpan? timeout = null)
        : base(apiKey, httpClient, timeout) { }

    public override async Task<IReadOnlyList<SearchResult>> DailyAsync(CancellationToken cancellationToken = default)
    {
        var market = Environment.GetEnvironmentVariable("WALLFLOW_BING_MARKET") ?? "ru-RU";
        var url = $"{ArchiveUrl}?format=js&idx=0&n=16&mkt={Uri.EscapeDataString(market)}";
        using var json = await GetJsonAsync(url, cancellationToken: cancellationToken).ConfigureAwait(false);
        var root = json.RootElement;
        var results = new List<SearchResult>();
        if (!root.TryGetProperty("images", out var images) || images.ValueKind != JsonValueKind.Array)
            return results;

        foreach (var entry in images.EnumerateArray())
        {
            var urlBase = SourcesHttp.String(entry, "urlbase");
            if (urlBase.Length == 0)
                urlBase = SourcesHttp.String(entry, "url");
            if (urlBase.Length == 0)
                continue;

            var copyright = SourcesHttp.String(entry, "copyright");
            if (copyright.Length == 0)
                copyright = SourcesHttp.String(root, "copyright");
            var (author, title) = SplitBingCopyright(copyright);
            results.Add(new SearchResult
            {
                Id = SourcesHttp.String(entry, "hsh", urlBase),
                Url = $"{urlBase}_1920x1080.jpg",
                ThumbnailUrl = $"{urlBase}_400x240.jpg",
                Title = title,
                Author = author,
                SourceUrl = SourcesHttp.String(entry, "clickurl", "https://www.bing.com"),
                Width = 1920,
                Height = 1080,
                Provider = Name,
                License = "Bing daily wallpaper"
            });
        }
        return results;
    }

    public override async Task<IReadOnlyList<SearchResult>> SearchAsync(
        string query, int page = 1, int perPage = 30, CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();
        return string.IsNullOrEmpty(query) || query.Equals("*", StringComparison.OrdinalIgnoreCase)
            ? (await DailyAsync(cancellationToken).ConfigureAwait(false)).Take(Math.Max(0, perPage)).ToArray()
            : [];
    }

    private static (string Author, string Title) SplitBingCopyright(string text)
    {
        var match = Regex.Match(text, @"^\s*(.*?)\s*\((.*)\)\s*$");
        return match.Success
            ? (match.Groups[1].Value.Trim(), match.Groups[2].Value.Trim())
            : (string.IsNullOrWhiteSpace(text) ? "Bing" : text.Trim(), "");
    }
}

public sealed class PicsumProvider : Provider
{
    public override string Name => "picsum";
    public override string Label => "Lorem Picsum";
    public override string Homepage => "https://picsum.photos";
    public override string Description => "Случайные качественные фотографии, без ключа";

    public PicsumProvider(string apiKey = "", HttpClient? httpClient = null, TimeSpan? timeout = null)
        : base(apiKey, httpClient, timeout) { }

    public override async Task<IReadOnlyList<SearchResult>> SearchAsync(
        string query, int page = 1, int perPage = 30, CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();
        if (!string.IsNullOrWhiteSpace(query))
        {
            var seed = Uri.EscapeDataString(query.Trim());
            return
            [
                new SearchResult
                {
                    Id = seed,
                    Url = $"https://picsum.photos/seed/{seed}/1920/1080",
                    ThumbnailUrl = $"https://picsum.photos/seed/{seed}/400/225",
                    Title = $"Random: {query}",
                    Author = "Lorem Picsum",
                    SourceUrl = "https://picsum.photos",
                    Width = 1920,
                    Height = 1080,
                    Provider = Name
                }
            ];
        }

        var limit = Math.Min(perPage, 60);
        var url = $"https://picsum.photos/v2/list?page={Math.Max(1, page)}&limit={limit}";
        JsonDocument json;
        try
        {
            json = await GetJsonAsync(url, cancellationToken: cancellationToken).ConfigureAwait(false);
        }
        catch (ProviderError)
        {
            return [];
        }

        using (json)
        {
            if (json.RootElement.ValueKind != JsonValueKind.Array)
                return [];
            return json.RootElement.EnumerateArray()
                .Where(entry => entry.ValueKind == JsonValueKind.Object && SourcesHttp.String(entry, "id").Length > 0)
                .Select(entry =>
                {
                    var id = SourcesHttp.String(entry, "id");
                    var author = SourcesHttp.String(entry, "author");
                    return new SearchResult
                    {
                        Id = id,
                        Url = $"https://picsum.photos/id/{Uri.EscapeDataString(id)}/1920/1080",
                        ThumbnailUrl = $"https://picsum.photos/id/{Uri.EscapeDataString(id)}/360/202",
                        Title = author.Length > 0 ? author : "Picsum",
                        Author = author,
                        SourceUrl = $"https://picsum.photos/photos/{Uri.EscapeDataString(id)}",
                        Width = SourcesHttp.Integer(entry, "width"),
                        Height = SourcesHttp.Integer(entry, "height"),
                        Provider = Name,
                        License = "Unsplash / Picsum"
                    };
                }).ToArray();
        }
    }
}

public sealed class LoremflickrProvider : Provider
{
    public override string Name => "loremflickr";
    public override string Label => "LoremFlickr";
    public override string Homepage => "https://loremflickr.com";
    public override string Description => "Поиск по ключевым словам, без ключа API";

    public LoremflickrProvider(string apiKey = "", HttpClient? httpClient = null, TimeSpan? timeout = null)
        : base(apiKey, httpClient, timeout) { }

    public override Task<IReadOnlyList<SearchResult>> SearchAsync(
        string query, int page = 1, int perPage = 30, CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();
        if (string.IsNullOrWhiteSpace(query))
            return Task.FromResult<IReadOnlyList<SearchResult>>([]);

        var tags = string.Join("/", Regex.Matches(query.ToLowerInvariant(), @"[\w-]+")
            .Select(match => match.Value).Take(3));
        if (tags.Length == 0)
            tags = "wallpaper";
        var pageLock = page > 1 ? $"?lock={page}" : "";
        IReadOnlyList<SearchResult> results = Enumerable.Range(0, Math.Max(0, Math.Min(perPage, 30)))
            .Select(index => new SearchResult
            {
                Id = $"{tags}-{page}-{index}",
                Url = $"https://loremflickr.com/1920/1080/{tags}/all{pageLock}?random={index}",
                ThumbnailUrl = $"https://loremflickr.com/360/202/{tags}/all{pageLock}?random={index}",
                Title = query,
                Author = "LoremFlickr",
                SourceUrl = "https://loremflickr.com",
                Width = 1920,
                Height = 1080,
                Provider = Name,
                License = "CC / Flickr"
            }).ToArray();
        return Task.FromResult(results);
    }
}

public sealed class OpenverseProvider : Provider
{
    public override string Name => "openverse";
    public override string Label => "Openverse";
    public override string Homepage => "https://openverse.org";
    public override string Description => "Поиск по свободным лицензиям (CC)";
    private const string ApiUrl = "https://api.openverse.org/v1/images/";

    public OpenverseProvider(string apiKey = "", HttpClient? httpClient = null, TimeSpan? timeout = null)
        : base(apiKey, httpClient, timeout) { }

    public override async Task<IReadOnlyList<SearchResult>> SearchAsync(
        string query, int page = 1, int perPage = 30, CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();
        if (string.IsNullOrWhiteSpace(query))
            return [];
        var url = $"{ApiUrl}?q={Uri.EscapeDataString(query.Trim())}&page={Math.Max(1, page)}" +
                  $"&page_size={Math.Min(perPage, 50)}&license_type=all-cc&mature=false";
        using var json = await GetJsonAsync(url,
            new Dictionary<string, string> { ["Accept"] = "application/json" },
            cancellationToken).ConfigureAwait(false);
        if (!json.RootElement.TryGetProperty("results", out var entries) ||
            entries.ValueKind != JsonValueKind.Array)
            return [];

        return entries.EnumerateArray()
            .Where(entry => entry.ValueKind == JsonValueKind.Object && SourcesHttp.String(entry, "url").Length > 0)
            .Select(entry => new SearchResult
            {
                Id = SourcesHttp.String(entry, "id"),
                Url = SourcesHttp.String(entry, "url"),
                ThumbnailUrl = SourcesHttp.String(entry, "thumbnail", SourcesHttp.String(entry, "url")),
                Title = SourcesHttp.String(entry, "title"),
                Author = SourcesHttp.String(entry, "creator"),
                SourceUrl = SourcesHttp.String(entry, "foreign_landing_url"),
                Width = SourcesHttp.Integer(entry, "width"),
                Height = SourcesHttp.Integer(entry, "height"),
                Provider = Name,
                License = SourcesHttp.String(entry, "license").ToUpperInvariant()
            }).ToArray();
    }
}

public sealed class NasaProvider : Provider
{
    public override string Name => "nasa";
    public override string Label => "NASA APOD";
    public override string Homepage => "https://apod.nasa.gov";
    public override string Description => "Астрономическое фото дня";

    public NasaProvider(string apiKey = "", HttpClient? httpClient = null, TimeSpan? timeout = null)
        : base(apiKey, httpClient, timeout) { }

    public override async Task<IReadOnlyList<SearchResult>> DailyAsync(CancellationToken cancellationToken = default)
    {
        var key = ApiKey.Length > 0 ? ApiKey : "DEMO_KEY";
        var url = $"https://api.nasa.gov/planetary/apod?api_key={Uri.EscapeDataString(key)}";
        using var json = await GetJsonAsync(url, cancellationToken: cancellationToken).ConfigureAwait(false);
        var data = json.RootElement;
        var imageUrl = SourcesHttp.String(data, "url");
        if (data.ValueKind != JsonValueKind.Object || imageUrl.Length == 0)
            return [];
        return
        [
            new SearchResult
            {
                Id = SourcesHttp.String(data, "date", DateOnly.FromDateTime(DateTime.Today).ToString("yyyy-MM-dd", CultureInfo.InvariantCulture)),
                Url = imageUrl,
                ThumbnailUrl = SourcesHttp.String(data, "thumbnail_url", imageUrl),
                Title = SourcesHttp.String(data, "title", "NASA APOD"),
                Author = "NASA",
                SourceUrl = SourcesHttp.String(data, "url", "https://apod.nasa.gov"),
                Width = SourcesHttp.Integer(data, "width", 1920),
                Height = SourcesHttp.Integer(data, "height", 1080),
                Provider = Name,
                License = "Public domain"
            }
        ];
    }

    public override Task<IReadOnlyList<SearchResult>> SearchAsync(
        string query, int page = 1, int perPage = 30, CancellationToken cancellationToken = default) =>
        DailyAsync(cancellationToken);
}

public sealed class UnsplashProvider : Provider
{
    public override string Name => "unsplash";
    public override string Label => "Unsplash";
    public override string Homepage => "https://unsplash.com";
    public override string Description => "Огромная библиотека фото (нужен ключ)";
    public override bool KeyRequired => true;
    public override string KeyHint => "Access Key из unsplash.com/developers";
    public override string EnvironmentVariable => "WALLFLOW_UNSPLASH_KEY";

    public UnsplashProvider(string apiKey = "", HttpClient? httpClient = null, TimeSpan? timeout = null)
        : base(apiKey, httpClient, timeout) { }

    public override async Task<IReadOnlyList<SearchResult>> SearchAsync(
        string query, int page = 1, int perPage = 30, CancellationToken cancellationToken = default)
    {
        RequireKey("Unsplash требует ключ доступа");
        var url = $"https://api.unsplash.com/search/photos?query={Uri.EscapeDataString(query.Length == 0 ? "wallpaper" : query)}" +
                  $"&per_page={Math.Min(perPage, 30)}&page={Math.Max(1, page)}";
        using var json = await GetJsonAsync(url, UnsplashHeaders(ApiKey), cancellationToken).ConfigureAwait(false);
        if (!json.RootElement.TryGetProperty("results", out var entries) ||
            entries.ValueKind != JsonValueKind.Array)
            return [];
        return entries.EnumerateArray().Select(MapPhoto).ToArray();
    }

    public override async Task<IReadOnlyList<SearchResult>> DailyAsync(CancellationToken cancellationToken = default)
    {
        if (ApiKey.Length == 0)
            return [];
        JsonDocument json;
        try
        {
            json = await GetJsonAsync("https://api.unsplash.com/photos/random?orientation=landscape",
                UnsplashHeaders(ApiKey), cancellationToken).ConfigureAwait(false);
        }
        catch (ProviderError)
        {
            return [];
        }

        using (json)
        {
            var entries = json.RootElement.ValueKind == JsonValueKind.Array
                ? json.RootElement.EnumerateArray().ToArray()
                : [json.RootElement];
            return entries.Select(entry =>
            {
                var result = MapPhoto(entry);
                return result with { Title = "Unsplash random", License = "Unsplash License" };
            }).ToArray();
        }
    }

    private static IReadOnlyDictionary<string, string> UnsplashHeaders(string apiKey) => new Dictionary<string, string>
    {
        ["Authorization"] = $"Client-ID {apiKey}",
        ["Accept-Version"] = "v1"
    };

    private void RequireKey(string message)
    {
        if (ApiKey.Length == 0)
            throw new ProviderError(message);
    }

    private SearchResult MapPhoto(JsonElement entry)
    {
        var urls = SourcesHttp.Property(entry, "urls");
        var user = SourcesHttp.Property(entry, "user");
        var links = SourcesHttp.Property(entry, "links");
        var title = SourcesHttp.String(entry, "description", SourcesHttp.String(entry, "alt_description"));
        if (title.Length > 120)
            title = title[..120];
        return new SearchResult
        {
            Id = SourcesHttp.String(entry, "id"),
            Url = SourcesHttp.String(urls, "raw", SourcesHttp.String(urls, "regular")),
            ThumbnailUrl = SourcesHttp.String(urls, "small"),
            Title = title,
            Author = SourcesHttp.String(user, "name"),
            SourceUrl = SourcesHttp.String(links, "html"),
            Width = SourcesHttp.Integer(entry, "width"),
            Height = SourcesHttp.Integer(entry, "height"),
            Provider = Name,
            License = "Unsplash License"
        };
    }
}

public sealed class PixabayProvider : Provider
{
    public override string Name => "pixabay";
    public override string Label => "Pixabay";
    public override string Homepage => "https://pixabay.com";
    public override string Description => "Фото и векторные обои (нужен ключ)";
    public override bool KeyRequired => true;
    public override string KeyHint => "API Key из pixabay.com/api/docs";
    public override string EnvironmentVariable => "WALLFLOW_PIXABAY_KEY";

    public PixabayProvider(string apiKey = "", HttpClient? httpClient = null, TimeSpan? timeout = null)
        : base(apiKey, httpClient, timeout) { }

    public override async Task<IReadOnlyList<SearchResult>> SearchAsync(
        string query, int page = 1, int perPage = 30, CancellationToken cancellationToken = default)
    {
        RequireKey();
        var search = query.Length == 0 ? "wallpaper" : query;
        var url = $"https://pixabay.com/api/?key={Uri.EscapeDataString(ApiKey)}&q={Uri.EscapeDataString(search)}" +
                  $"&image_type=photo&orientation=horizontal&min_width=1920&per_page={Math.Min(perPage, 200)}&page={Math.Max(1, page)}";
        using var json = await GetJsonAsync(url, cancellationToken: cancellationToken).ConfigureAwait(false);
        if (!json.RootElement.TryGetProperty("hits", out var entries) || entries.ValueKind != JsonValueKind.Array)
            return [];
        return entries.EnumerateArray()
            .Where(entry => SourcesHttp.String(entry, "largeImageURL").Length > 0)
            .Select(entry => new SearchResult
            {
                Id = SourcesHttp.String(entry, "id"),
                Url = SourcesHttp.String(entry, "largeImageURL", SourcesHttp.String(entry, "webformatURL")),
                ThumbnailUrl = SourcesHttp.String(entry, "previewURL"),
                Title = FirstTag(SourcesHttp.String(entry, "tags")),
                Author = SourcesHttp.String(entry, "user"),
                SourceUrl = SourcesHttp.String(entry, "pageURL"),
                Width = SourcesHttp.Integer(entry, "width"),
                Height = SourcesHttp.Integer(entry, "height"),
                Provider = Name,
                License = "Pixabay License"
            }).ToArray();
    }

    private void RequireKey()
    {
        if (ApiKey.Length == 0)
            throw new ProviderError("Pixabay требует ключ API");
    }

    private static string FirstTag(string tags)
    {
        var first = tags.Split(',', 2)[0].Trim();
        return first.Length == 0 ? "Pixabay" : first;
    }
}

public sealed class PexelsProvider : Provider
{
    public override string Name => "pexels";
    public override string Label => "Pexels";
    public override string Homepage => "https://pexels.com";
    public override string Description => "Бесплатные фото высокого разрешения (нужен ключ)";
    public override bool KeyRequired => true;
    public override string KeyHint => "API Key из pexels.com/api";
    public override string EnvironmentVariable => "WALLFLOW_PEXELS_KEY";

    public PexelsProvider(string apiKey = "", HttpClient? httpClient = null, TimeSpan? timeout = null)
        : base(apiKey, httpClient, timeout) { }

    public override async Task<IReadOnlyList<SearchResult>> SearchAsync(
        string query, int page = 1, int perPage = 30, CancellationToken cancellationToken = default)
    {
        if (ApiKey.Length == 0)
            throw new ProviderError("Pexels требует ключ API");
        var search = query.Length == 0 ? "wallpaper" : query;
        var url = $"https://api.pexels.com/v1/search?query={Uri.EscapeDataString(search)}" +
                  $"&per_page={Math.Min(perPage, 80)}&page={Math.Max(1, page)}";
        using var json = await GetJsonAsync(url,
            new Dictionary<string, string> { ["Authorization"] = ApiKey },
            cancellationToken).ConfigureAwait(false);
        if (!json.RootElement.TryGetProperty("photos", out var entries) || entries.ValueKind != JsonValueKind.Array)
            return [];
        return entries.EnumerateArray().Select(entry =>
        {
            var sources = SourcesHttp.Property(entry, "src");
            return new SearchResult
            {
                Id = SourcesHttp.String(entry, "id"),
                Url = SourcesHttp.String(sources, "original"),
                ThumbnailUrl = SourcesHttp.String(sources, "medium"),
                Title = SourcesHttp.String(entry, "alt", "Pexels"),
                Author = SourcesHttp.String(entry, "photographer"),
                SourceUrl = SourcesHttp.String(entry, "url"),
                Width = SourcesHttp.Integer(entry, "width"),
                Height = SourcesHttp.Integer(entry, "height"),
                Provider = Name,
                License = "Pexels License"
            };
        }).Where(result => result.Url.Length > 0).ToArray();
    }
}

public static class WallpaperSources
{
    public static IReadOnlyList<Type> Providers { get; } =
    [
        typeof(BingProvider),
        typeof(OpenverseProvider),
        typeof(LoremflickrProvider),
        typeof(PicsumProvider),
        typeof(NasaProvider),
        typeof(UnsplashProvider),
        typeof(PixabayProvider),
        typeof(PexelsProvider)
    ];

    public static IReadOnlyList<Provider> BuildProviders(
        IReadOnlyDictionary<string, string>? apiKeys = null,
        HttpClient? httpClient = null,
        TimeSpan? timeout = null)
    {
        string Key(string name) => apiKeys is not null && apiKeys.TryGetValue(name, out var key) ? key : "";
        return
        [
            new BingProvider(Key("bing"), httpClient, timeout),
            new OpenverseProvider(Key("openverse"), httpClient, timeout),
            new LoremflickrProvider(Key("loremflickr"), httpClient, timeout),
            new PicsumProvider(Key("picsum"), httpClient, timeout),
            new NasaProvider(Key("nasa"), httpClient, timeout),
            new UnsplashProvider(Key("unsplash"), httpClient, timeout),
            new PixabayProvider(Key("pixabay"), httpClient, timeout),
            new PexelsProvider(Key("pexels"), httpClient, timeout)
        ];
    }

    public static Provider? ProviderByName(
        string name,
        string apiKey = "",
        HttpClient? httpClient = null,
        TimeSpan? timeout = null) =>
        name switch
        {
            "bing" => new BingProvider(apiKey, httpClient, timeout),
            "openverse" => new OpenverseProvider(apiKey, httpClient, timeout),
            "loremflickr" => new LoremflickrProvider(apiKey, httpClient, timeout),
            "picsum" => new PicsumProvider(apiKey, httpClient, timeout),
            "nasa" => new NasaProvider(apiKey, httpClient, timeout),
            "unsplash" => new UnsplashProvider(apiKey, httpClient, timeout),
            "pixabay" => new PixabayProvider(apiKey, httpClient, timeout),
            "pexels" => new PexelsProvider(apiKey, httpClient, timeout),
            _ => null
        };

    public static async Task<string?> DownloadAsync(
        SearchResult result,
        string destination,
        Action<long, long>? progress = null,
        CancellationToken cancellationToken = default,
        HttpClient? httpClient = null,
        TimeSpan? timeout = null)
    {
        cancellationToken.ThrowIfCancellationRequested();
        if (result.Url.Length == 0)
            return null;
        var destinationPath = Path.GetFullPath(destination);
        Directory.CreateDirectory(Path.GetDirectoryName(destinationPath)!);
        var temporaryPath = destinationPath + ".part";
        var client = httpClient ?? SourcesHttp.SharedClient;
        var requestTimeout = timeout ?? SourcesHttp.DefaultTimeout;
        using var timeoutCts = requestTimeout == Timeout.InfiniteTimeSpan
            ? null
            : new CancellationTokenSource(requestTimeout);
        using var linkedCts = timeoutCts is null
            ? null
            : CancellationTokenSource.CreateLinkedTokenSource(cancellationToken, timeoutCts.Token);
        var requestToken = linkedCts?.Token ?? cancellationToken;

        try
        {
            using var response = await SourcesHttp.SendAsync(
                client, result.Url, null, Timeout.InfiniteTimeSpan, requestToken).ConfigureAwait(false);
            var total = response.Content.Headers.ContentLength ?? 0;
            long read = 0;
            await using (var output = new FileStream(temporaryPath, FileMode.Create, FileAccess.Write, FileShare.None,
                             32 * 1024, FileOptions.Asynchronous | FileOptions.SequentialScan))
            await using (var input = await response.Content.ReadAsStreamAsync(requestToken).ConfigureAwait(false))
            {
                var buffer = new byte[64 * 512];
                while (true)
                {
                    var count = await input.ReadAsync(buffer, requestToken).ConfigureAwait(false);
                    if (count == 0)
                        break;
                    await output.WriteAsync(buffer.AsMemory(0, count), requestToken).ConfigureAwait(false);
                    read += count;
                    if (progress is not null && total > 0)
                        progress(read, total);
                }
            }
            if (read == 0)
                throw new ProviderError("Пустой ответ сервера");
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            TryDelete(temporaryPath);
            throw;
        }
        catch (Exception exception) when (exception is ProviderError or HttpRequestException or IOException or UnauthorizedAccessException or OperationCanceledException)
        {
            TryDelete(temporaryPath);
            return null;
        }

        File.Move(temporaryPath, destinationPath, overwrite: true);
        return destinationPath;
    }

    public static string GuessExtension(string url, string fallback = ".jpg")
    {
        string path;
        try
        {
            path = new Uri(url, UriKind.Absolute).AbsolutePath;
        }
        catch (UriFormatException)
        {
            path = url.Split('?', '#')[0];
        }

        var suffix = Path.GetExtension(Uri.UnescapeDataString(path)).ToLowerInvariant();
        if (suffix == ".jpeg")
            return ".jpg";
        if (suffix is ".jpg" or ".png" or ".webp" or ".gif" or ".avif" or ".bmp")
            return suffix;

        var mime = suffix switch
        {
            ".tif" or ".tiff" => "image/tiff",
            ".svg" => "image/svg+xml",
            ".ico" => "image/vnd.microsoft.icon",
            _ => ""
        };
        return mime switch
        {
            "image/tiff" => ".tif",
            "image/svg+xml" => ".svg",
            "image/vnd.microsoft.icon" => ".ico",
            _ => fallback
        };
    }

    public static string SuggestFilename(SearchResult result)
    {
        var title = SafeFilename(
            result.Title.Length > 0 ? result.Title : result.Provider.Length > 0 ? result.Provider : "wallpaper",
            "wallpaper");
        return $"{result.Provider}-{title}{GuessExtension(result.Url)}";
    }

    public static Wallpaper ResultToWallpaper(SearchResult result, string localPath)
    {
        var wallpaper = Wallpaper.FromPath(localPath);
        wallpaper.Kind = "image";
        wallpaper.Name = result.Title.Length > 0 ? result.Title : wallpaper.Name;
        wallpaper.Author = result.Author;
        wallpaper.SourceUrl = result.SourceUrl;
        wallpaper.Tags = result.License.Length > 0 ? [result.Provider, result.License] : [result.Provider];
        return wallpaper;
    }

    private static string SafeFilename(string name, string fallback)
    {
        var invalid = new HashSet<char>("<>:\"/\\|?*".Concat(Enumerable.Range(0, 32).Select(i => (char)i)));
        var cleaned = new string(name.Select(character => invalid.Contains(character) ? '_' : character).ToArray())
            .Trim(' ', '.');
        cleaned = Regex.Replace(cleaned, @"\s+", " ");
        var reserved = cleaned.ToUpperInvariant();
        if (cleaned.Length == 0 || reserved is "CON" or "PRN" or "AUX" or "NUL" ||
            Regex.IsMatch(reserved, @"^(COM|LPT)[1-9]$"))
            return fallback;
        return cleaned.Length > 120 ? cleaned[..120] : cleaned;
    }

    private static void TryDelete(string path)
    {
        try { File.Delete(path); }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }
}

internal static class SourcesHttp
{
    internal static readonly TimeSpan DefaultTimeout = TimeSpan.FromSeconds(25);
    internal static readonly HttpClient SharedClient = CreateClient();
    private const string UserAgent = "Wallflow/1.0 (+https://github.com/wallflow/wallflow)";

    internal static async Task<JsonDocument> GetJsonAsync(
        HttpClient client,
        string url,
        IReadOnlyDictionary<string, string>? headers,
        TimeSpan timeout,
        CancellationToken cancellationToken)
    {
        try
        {
            using var timeoutCts = timeout == Timeout.InfiniteTimeSpan
                ? null
                : new CancellationTokenSource(timeout);
            using var linkedCts = timeoutCts is null
                ? null
                : CancellationTokenSource.CreateLinkedTokenSource(cancellationToken, timeoutCts.Token);
            var requestToken = linkedCts?.Token ?? cancellationToken;
            using var response = await SendAsync(
                client, url, headers, Timeout.InfiniteTimeSpan, requestToken).ConfigureAwait(false);
            await using var stream = await response.Content.ReadAsStreamAsync(requestToken).ConfigureAwait(false);
            return await JsonDocument.ParseAsync(stream, cancellationToken: requestToken).ConfigureAwait(false);
        }
        catch (JsonException exception)
        {
            throw new ProviderError("Сервер вернул некорректный JSON", exception);
        }
        catch (OperationCanceledException exception) when (!cancellationToken.IsCancellationRequested)
        {
            throw new ProviderError("Время ожидания запроса истекло", exception);
        }
        catch (IOException exception)
        {
            throw new ProviderError($"Сеть недоступна: {exception.Message}", exception);
        }
    }

    internal static async Task<HttpResponseMessage> SendAsync(
        HttpClient client,
        string url,
        IReadOnlyDictionary<string, string>? headers,
        TimeSpan timeout,
        CancellationToken cancellationToken)
    {
        using var timeoutCts = timeout == Timeout.InfiniteTimeSpan ? null : new CancellationTokenSource(timeout);
        using var linkedCts = timeoutCts is null
            ? null
            : CancellationTokenSource.CreateLinkedTokenSource(cancellationToken, timeoutCts.Token);
        var token = linkedCts?.Token ?? cancellationToken;
        using var request = new HttpRequestMessage(HttpMethod.Get, url);
        request.Headers.UserAgent.ParseAdd(UserAgent);
        if (headers is not null)
        {
            foreach (var (name, value) in headers)
            {
                request.Headers.TryAddWithoutValidation(name, value);
            }
        }

        try
        {
            var response = await client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, token)
                .ConfigureAwait(false);
            if (!response.IsSuccessStatusCode)
            {
                var host = Uri.TryCreate(url, UriKind.Absolute, out var uri) ? uri.Host : url;
                var status = (int)response.StatusCode;
                response.Dispose();
                throw new ProviderError($"HTTP {status} от {host}");
            }
            return response;
        }
        catch (OperationCanceledException exception) when (!cancellationToken.IsCancellationRequested)
        {
            throw new ProviderError("Время ожидания запроса истекло", exception);
        }
        catch (HttpRequestException exception)
        {
            throw new ProviderError($"Сеть недоступна: {exception.Message}", exception);
        }
        catch (IOException exception)
        {
            throw new ProviderError(exception.Message, exception);
        }
    }

    internal static JsonElement Property(JsonElement element, string name) =>
        element.ValueKind == JsonValueKind.Object &&
        element.TryGetProperty(name, out var value) &&
        value.ValueKind == JsonValueKind.Object
            ? value
            : default;

    internal static string String(JsonElement element, string property, string fallback = "")
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(property, out var value) ||
            value.ValueKind is JsonValueKind.Null or JsonValueKind.Undefined)
            return fallback;
        return value.ValueKind == JsonValueKind.String
            ? value.GetString() ?? fallback
            : value.ToString();
    }

    internal static int Integer(JsonElement element, string property, int fallback = 0)
    {
        var value = String(element, property);
        return int.TryParse(value, NumberStyles.Integer, CultureInfo.InvariantCulture, out var number)
            ? number
            : fallback;
    }

    private static HttpClient CreateClient()
    {
        var client = new HttpClient { Timeout = Timeout.InfiniteTimeSpan };
        client.DefaultRequestHeaders.UserAgent.ParseAdd(UserAgent);
        return client;
    }
}

using System.Globalization;
using System.Text.Json;

namespace Wallflow.App.Core.Scenes;

public sealed class SceneBackground
{
    public string Kind { get; set; } = "solid";
    public string Color { get; set; } = "#101018";
    public List<string> Colors { get; set; } = [];
    public double Angle { get; set; } = 45;

    public static SceneBackground FromObject(object? value)
    {
        if (value is not JsonElement element || element.ValueKind != JsonValueKind.Object)
            return value as SceneBackground ?? new SceneBackground();

        var background = new SceneBackground
        {
            Kind = ReadString(element, "type", "solid"),
            Color = ReadString(element, "color", "#101018"),
            Angle = ReadDouble(element, "angle", 45)
        };
        if (element.TryGetProperty("colors", out var colors))
        {
            if (colors.ValueKind == JsonValueKind.String)
                background.Colors.Add(colors.GetString() ?? "");
            else if (colors.ValueKind == JsonValueKind.Array)
                background.Colors.AddRange(colors.EnumerateArray()
                    .Take(8)
                    .Select(item => item.ValueKind == JsonValueKind.String ? item.GetString() ?? "" : item.ToString()));
        }
        return background;
    }

    private static string ReadString(JsonElement element, string key, string fallback) =>
        element.TryGetProperty(key, out var value) && value.ValueKind != JsonValueKind.Null
            ? value.ToString()
            : fallback;

    private static double ReadDouble(JsonElement element, string key, double fallback) =>
        element.TryGetProperty(key, out var value) &&
        double.TryParse(value.ToString(), NumberStyles.Float, CultureInfo.InvariantCulture, out var number)
            ? number
            : fallback;
}

public sealed class SceneSpec
{
    public static IReadOnlyList<string> BuiltinTypes { get; } =
        ["particles", "starfield", "waves", "rain", "bubbles", "gradient", "plasma", "breathing", "grid"];

    public string Name { get; set; } = "Scene";
    public string Kind { get; set; } = "particles";
    public int Fps { get; set; } = 30;
    public SceneBackground Background { get; set; } = new();
    public Dictionary<string, object?> Params { get; set; } = new(StringComparer.Ordinal);
    public string? Source { get; set; }

    public object? Param(string key, object? fallback)
    {
        if (!Params.TryGetValue(key, out var value) || value is null)
            return fallback;
        if (fallback is bool)
            return TryBoolean(value, out var boolean) ? boolean : fallback;
        if (fallback is int)
            return TryDouble(value, out var integer) ? (int)integer : fallback;
        if (fallback is float)
            return TryDouble(value, out var single) ? (float)single : fallback;
        if (fallback is double)
            return TryDouble(value, out var number) ? number : fallback;
        if (fallback is string)
            return value is string text ? text : fallback;
        return value;
    }

    public double Number(string key, double fallback) =>
        TryDouble(Param(key, fallback), out var value) ? value : fallback;

    public bool Boolean(string key, bool fallback) =>
        TryBoolean(Param(key, fallback), out var value) ? value : fallback;

    public static SceneSpec Load(string path)
    {
        var fullPath = Path.GetFullPath(path);
        var spec = new SceneSpec { Name = Path.GetFileNameWithoutExtension(fullPath) };
        try
        {
            using var document = JsonDocument.Parse(File.ReadAllText(fullPath));
            var root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object)
                return spec;

            spec.Name = ReadString(root, "name", spec.Name);
            spec.Kind = NormalizeKind(ReadString(root, "type", "particles"));
            spec.Fps = ClampInt(ReadValue(root, "fps"), 1, 60, 30);
            spec.Background = root.TryGetProperty("background", out var background)
                ? SceneBackground.FromObject(background)
                : new SceneBackground();
            if (root.TryGetProperty("params", out var parameters) && parameters.ValueKind == JsonValueKind.Object)
            {
                foreach (var property in parameters.EnumerateObject())
                    spec.Params[property.Name] = FromJson(property.Value);
            }
            spec.Source = fullPath;
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or JsonException)
        {
            // A malformed or unavailable scene remains usable with safe defaults.
        }
        return spec;
    }

    public static SceneSpec Builtin(string kind, string? name = null, int fps = 30,
        SceneBackground? background = null, IDictionary<string, object?>? parameters = null) =>
        new()
        {
            Name = string.IsNullOrEmpty(name) ? NormalizeKind(kind) : name,
            Kind = NormalizeKind(kind),
            Fps = Math.Clamp(fps, 1, 60),
            Background = background ?? new SceneBackground(),
            Params = parameters is null
                ? new Dictionary<string, object?>(StringComparer.Ordinal)
                : new Dictionary<string, object?>(parameters, StringComparer.Ordinal)
        };

    public Dictionary<string, object?> ToDictionary() => new(StringComparer.Ordinal)
    {
        ["name"] = Name,
        ["type"] = Kind,
        ["fps"] = Math.Clamp(Fps, 1, 60),
        ["background"] = new Dictionary<string, object?>
        {
            ["type"] = Background.Kind,
            ["color"] = Background.Color,
            ["colors"] = Background.Colors.Take(8).ToList(),
            ["angle"] = Background.Angle
        },
        ["params"] = Params
    };

    public string ToJson() => JsonSerializer.Serialize(ToDictionary(), new JsonSerializerOptions { WriteIndented = true });

    public string Save(string path)
    {
        var fullPath = Path.GetFullPath(path);
        Directory.CreateDirectory(Path.GetDirectoryName(fullPath)!);
        File.WriteAllText(fullPath, ToJson());
        return fullPath;
    }

    public static string NormalizeKind(string? kind) =>
        BuiltinTypes.Contains(kind?.ToLowerInvariant() ?? "", StringComparer.Ordinal)
            ? kind!.ToLowerInvariant()
            : "particles";

    internal static int ClampInt(object? value, int minimum, int maximum, int fallback) =>
        TryDouble(value, out var number) ? Math.Clamp((int)number, minimum, maximum) : fallback;

    internal static double ClampNumber(object? value, double minimum, double maximum, double fallback) =>
        TryDouble(value, out var number) && double.IsFinite(number) ? Math.Clamp(number, minimum, maximum) : fallback;

    internal static bool TryDouble(object? value, out double number)
    {
        if (value is JsonElement element)
            value = FromJson(element);
        if (value is IConvertible convertible && value is not bool)
        {
            try
            {
                number = convertible.ToDouble(CultureInfo.InvariantCulture);
                return double.IsFinite(number);
            }
            catch (FormatException) { }
            catch (InvalidCastException) { }
            catch (OverflowException) { }
        }
        number = 0;
        return false;
    }

    private static bool TryBoolean(object? value, out bool boolean)
    {
        if (value is JsonElement element)
            value = FromJson(element);
        if (value is bool result)
        {
            boolean = result;
            return true;
        }
        if (value is string text && bool.TryParse(text, out result))
        {
            boolean = result;
            return true;
        }
        boolean = false;
        return false;
    }

    private static object? FromJson(JsonElement element) => element.ValueKind switch
    {
        JsonValueKind.String => element.GetString(),
        JsonValueKind.True => true,
        JsonValueKind.False => false,
        JsonValueKind.Number when element.TryGetInt64(out var integer) => integer,
        JsonValueKind.Number => element.GetDouble(),
        JsonValueKind.Array => element.EnumerateArray().Select(FromJson).ToList(),
        JsonValueKind.Object => element.EnumerateObject().ToDictionary(item => item.Name, item => FromJson(item.Value)),
        _ => null
    };

    private static string ReadString(JsonElement element, string key, string fallback) =>
        element.TryGetProperty(key, out var value) && value.ValueKind != JsonValueKind.Null
            ? value.ToString()
            : fallback;

    private static object? ReadValue(JsonElement element, string key) =>
        element.TryGetProperty(key, out var value) ? FromJson(value) : null;
}

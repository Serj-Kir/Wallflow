namespace Wallflow.App.Core.Scenes;

public static class BuiltinScenes
{
    public static IReadOnlyDictionary<string, SceneSpec> All { get; } = CreateScenes();

    public static string WriteScene(string name, string destination) =>
        All.TryGetValue(name, out var spec)
            ? spec.Save(destination)
            : throw new KeyNotFoundException($"No built-in scene named '{name}'.");

    private static IReadOnlyDictionary<string, SceneSpec> CreateScenes()
    {
        var scenes = new List<SceneSpec>
        {
            SceneSpec.Builtin("particles", "Частицы", 30,
                Gradient("#0b1020", "#131a33", 60),
                Params(("count", 140), ("speed", 0.9), ("hue", 210), ("saturation", 0.65))),
            SceneSpec.Builtin("starfield", "Звёздное небо", 30,
                Gradient("#02030a", "#0a1030", 90),
                Params(("count", 280), ("speed", 1.0))),
            SceneSpec.Builtin("waves", "Волны", 30,
                Gradient("#04121f", "#0a2a44", 30),
                Params(("count", 8), ("hue", 195), ("amplitude", 70), ("wavelength", 460))),
            SceneSpec.Builtin("bubbles", "Пузыри", 30,
                Gradient("#081426", "#123a5c", 45),
                Params(("count", 36), ("speed", 0.8))),
            SceneSpec.Builtin("rain", "Дождь", 30,
                Solid("#0a0e16"),
                Params(("count", 200), ("speed", 1.1), ("hue", 205))),
            SceneSpec.Builtin("plasma", "Плазма", 24,
                Solid("#05050c"),
                Params(("hue", 270), ("scale", 3.2), ("cell", 16))),
            SceneSpec.Builtin("breathing", "Дыхание", 24,
                Gradient("#1a0b2e", "#2b1055", 60),
                Params(("speed", 0.7))),
            SceneSpec.Builtin("grid", "Сетка", 30,
                Solid("#04080c"),
                Params(("hue", 160), ("cells", 26), ("speed", 0.6))),
            SceneSpec.Builtin("gradient", "Gradient", 24,
                Gradient("#0f1020", "#2b1055", 45),
                Params(("speed", 0.15)))
        };
        return scenes.ToDictionary(scene => scene.Name, scene => scene, StringComparer.Ordinal);
    }

    private static SceneBackground Solid(string color) => new() { Kind = "solid", Color = color };
    private static SceneBackground Gradient(string first, string second, double angle) =>
        new() { Kind = "gradient", Color = first, Colors = [first, second], Angle = angle };
    private static Dictionary<string, object?> Params(params (string Key, object? Value)[] entries) =>
        entries.ToDictionary(entry => entry.Key, entry => entry.Value, StringComparer.Ordinal);
}

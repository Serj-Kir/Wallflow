namespace Wallflow.App.Core;

public sealed record WallflowCommandLine(
    bool ShowHelp = false,
    bool ShowVersion = false,
    bool Minimized = false,
    bool Hidden = false,
    string? DataDirectory = null,
    string? SetPath = null,
    string? ProfileName = null,
    bool ListMonitors = false,
    bool ResetSettings = false,
    bool Verbose = false,
    bool Quit = false)
{
    public static bool TryParse(
        IReadOnlyList<string> args,
        out WallflowCommandLine options,
        out string? error)
    {
        var values = new Dictionary<string, string?>(StringComparer.Ordinal);
        var flags = new HashSet<string>(StringComparer.Ordinal);
        error = null;

        for (var index = 0; index < args.Count; index++)
        {
            var argument = args[index];
            var separator = argument.IndexOf('=');
            var name = separator >= 0 ? argument[..separator] : argument;
            var inlineValue = separator >= 0 ? argument[(separator + 1)..] : null;
            switch (name)
            {
                case "-h":
                    name = "--help";
                    goto case "--help";
                case "--help":
                case "--version":
                case "--minimized":
                case "--hidden":
                case "--list-monitors":
                case "--reset-settings":
                case "--quit":
                case "-v":
                    flags.Add(name == "-v" ? "--verbose" : name);
                    break;
                case "--verbose":
                    flags.Add(name);
                    break;
                case "--data-dir":
                case "--set":
                case "--profile":
                    var value = inlineValue;
                    if (value is null && index + 1 < args.Count && !args[index + 1].StartsWith("-", StringComparison.Ordinal))
                        value = args[++index];
                    if (string.IsNullOrWhiteSpace(value))
                    {
                        error = $"Option {name} requires a value.";
                        options = new WallflowCommandLine();
                        return false;
                    }
                    values[name] = value;
                    break;
                default:
                    error = $"Unknown option: {argument}";
                    options = new WallflowCommandLine();
                    return false;
            }
        }

        var actionCount = (flags.Contains("--list-monitors") ? 1 : 0) +
                          (values.ContainsKey("--set") ? 1 : 0) +
                          (values.ContainsKey("--profile") ? 1 : 0) +
                          (flags.Contains("--quit") ? 1 : 0);
        if (actionCount > 1)
        {
            error = "--quit, --list-monitors, --set, and --profile cannot be combined.";
            options = new WallflowCommandLine();
            return false;
        }

        options = new WallflowCommandLine(
            ShowHelp: flags.Contains("--help"),
            ShowVersion: flags.Contains("--version"),
            Minimized: flags.Contains("--minimized"),
            Hidden: flags.Contains("--hidden"),
            DataDirectory: values.GetValueOrDefault("--data-dir"),
            SetPath: values.GetValueOrDefault("--set"),
            ProfileName: values.GetValueOrDefault("--profile"),
            ListMonitors: flags.Contains("--list-monitors"),
            ResetSettings: flags.Contains("--reset-settings"),
            Verbose: flags.Contains("--verbose"),
            Quit: flags.Contains("--quit"));
        return true;
    }

    public static string Usage =>
        """
        Wallflow - live wallpaper engine

        Options:
          --version             Show the application version
          --minimized           Start minimized to the system tray
          --hidden              Start hidden to the system tray
          --data-dir PATH       Use a custom data directory
          --set PATH            Apply a wallpaper file or HTML project
          --profile NAME        Start with the named profile
          --list-monitors       List detected displays and exit
          --reset-settings      Reset settings to defaults before startup
          --verbose, -v         Enable verbose startup logging
          --quit                Quit the running Wallflow instance
          --help, -h            Show this help
        """;
}

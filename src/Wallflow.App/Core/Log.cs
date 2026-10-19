namespace Wallflow.App.Core;

public enum LogLevel
{
    Debug,
    Info,
    Warn,
    Error
}

/// <summary>
/// Minimal rotating file logger: the same <c>logs/wallflow.log</c> location the
/// application used before, capped at 2 MB with three rotated files.
/// </summary>
public static class Log
{
    private const long MaximumBytes = 2_000_000;
    private const int MaximumFiles = 3;

    private static readonly object Gate = new();
    private static LogLevel _level = LogLevel.Info;
    private static bool _verbose;

    public static string LogFile => Path.Combine(WallflowPaths.LogDirectory, "wallflow.log");

    /// <summary>Everything at or above <paramref name="level"/> is written; verbose also echoes to the console.</summary>
    public static void Configure(LogLevel level, bool verbose = false)
    {
        _level = verbose ? LogLevel.Debug : level;
        _verbose = verbose;
    }

    public static void Debug(string message) => Write(LogLevel.Debug, message);
    public static void Info(string message) => Write(LogLevel.Info, message);
    public static void Warn(string message) => Write(LogLevel.Warn, message);
    public static void Error(string message) => Write(LogLevel.Error, message);

    public static void Error(string message, Exception exception) =>
        Write(LogLevel.Error, $"{message}: {exception.GetType().Name}: {exception.Message}");

    public static void Warn(string message, Exception exception) =>
        Write(LogLevel.Warn, $"{message}: {exception.GetType().Name}: {exception.Message}");

    private static void Write(LogLevel level, string message)
    {
        if (level < _level)
            return;

        var line = string.Create(
            System.Globalization.CultureInfo.InvariantCulture,
            $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {level,-5} {message}");
        lock (Gate)
        {
            try
            {
                var path = LogFile;
                Directory.CreateDirectory(Path.GetDirectoryName(path)!);
                Rotate(path);
                File.AppendAllText(path, line + Environment.NewLine);
            }
            catch (Exception exception) when (
                exception is IOException or UnauthorizedAccessException or NotSupportedException)
            {
                // Logging must never take the application down.
            }
        }

        if (_verbose || level >= LogLevel.Warn)
        {
            var writer = level >= LogLevel.Warn ? Console.Error : Console.Out;
            try
            {
                writer.WriteLine(line);
            }
            catch (IOException)
            {
            }
        }
    }

    private static void Rotate(string path)
    {
        if (!File.Exists(path) || new FileInfo(path).Length < MaximumBytes)
            return;
        for (var index = MaximumFiles - 1; index >= 1; index--)
        {
            var source = $"{path}.{index}";
            if (!File.Exists(source))
                continue;
            File.Move(source, $"{path}.{index + 1}", overwrite: true);
        }
        File.Move(path, $"{path}.1", overwrite: true);
    }
}

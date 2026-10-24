using Avalonia;
using System;
using Wallflow.App.Core;

namespace Wallflow.App;

class Program
{
    internal static SingleInstanceGuard? InstanceGuard { get; private set; }

    // Initialization code. Don't use any Avalonia, third-party APIs or any
    // SynchronizationContext-reliant code before AppMain is called: things aren't initialized
    // yet and stuff might break.
    [STAThread]
    public static void Main(string[] args)
    {
        ConsoleSupport.AttachToParentConsole();

        if (!WallflowCommandLine.TryParse(args, out var options, out var error))
        {
            Console.Error.WriteLine(error);
            Console.Error.WriteLine(WallflowCommandLine.Usage);
            Environment.ExitCode = 2;
            return;
        }

        Log.Configure(LogLevel.Info, options.Verbose);
        AppDomain.CurrentDomain.UnhandledException += (_, eventArgs) =>
            Log.Error($"Unhandled exception: {eventArgs.ExceptionObject}");
        TaskScheduler.UnobservedTaskException += (_, eventArgs) =>
            Log.Error($"Unobserved background task exception: {eventArgs.Exception}");

        if (options.ShowHelp)
        {
            Console.WriteLine(WallflowCommandLine.Usage);
            return;
        }

        if (options.ShowVersion)
        {
            Console.WriteLine("Wallflow 1.0.0");
            return;
        }

        if (options.DataDirectory is { } dataDirectory)
            Environment.SetEnvironmentVariable("WALLFLOW_DATA_DIR", ExpandPath(dataDirectory));

        if (options.Quit)
        {
            if (!SingleInstanceGuard.RequestQuit())
            {
                Console.Error.WriteLine("Wallflow is not running or did not respond to the quit request.");
                Environment.ExitCode = 1;
            }
            else
            {
                Console.WriteLine("Closing Wallflow...");
            }
            return;
        }

        using var guard = new SingleInstanceGuard();
        InstanceGuard = guard;
        if (!guard.IsPrimaryInstance)
        {
            Console.Error.WriteLine("Another Wallflow instance is already running.");
            Environment.ExitCode = 1;
            return;
        }

        if (options.ResetSettings)
            new WallflowConfig().ResetDefaults();

        App.StartMinimized = options.Minimized || options.Hidden;
        App.StartupCommand = options;
        Log.Info($"Wallflow 1.0.0 starting (data: {WallflowPaths.DataDirectory}, " +
                 $"desktop: {DesktopEnvironment.Detect().Desktop}, session: {DesktopEnvironment.SessionType()})");
        Environment.ExitCode = BuildAvaloniaApp().StartWithClassicDesktopLifetime([]);
        Log.Info("Wallflow stopped");
        InstanceGuard = null;
    }

    private static string ExpandPath(string path)
    {
        if (path == "~")
            return Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        if (path.StartsWith("~\\", StringComparison.Ordinal) ||
            path.StartsWith("~/", StringComparison.Ordinal))
            path = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), path[2..]);
        return Path.GetFullPath(path);
    }

    // Avalonia configuration, don't remove; also used by visual designer.
    public static AppBuilder BuildAvaloniaApp()
        => AppBuilder.Configure<App>()
            .UsePlatformDetect()
            .WithInterFont()
            .LogToTrace();
}

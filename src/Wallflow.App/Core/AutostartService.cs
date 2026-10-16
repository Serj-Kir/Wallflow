using System.Diagnostics;
using System.IO.Pipes;
using System.Security;
using System.Security.Principal;
using System.Text;
using System.Xml;

namespace Wallflow.App.Core;

public static class AutostartService
{
    public static string EntryPath
    {
        get
        {
            if (OperatingSystem.IsWindows())
                return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Startup), "wallflow.cmd");
            if (OperatingSystem.IsMacOS())
                return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                    "Library", "LaunchAgents", "org.wallflow.app.plist");
            return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                ".config", "autostart", "wallflow.desktop");
        }
    }

    public static bool IsEnabled => File.Exists(EntryPath);

    public static void Enable()
    {
        var path = EntryPath;
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var temporaryPath = path + ".tmp";
        var contents = OperatingSystem.IsWindows()
            ? BuildWindowsCommand()
            : OperatingSystem.IsMacOS()
                ? BuildMacLaunchAgent()
                : BuildDesktopEntry();
        File.WriteAllText(temporaryPath, contents, new UTF8Encoding(encoderShouldEmitUTF8Identifier: false));
        File.Move(temporaryPath, path, overwrite: true);
    }

    public static void Disable()
    {
        if (File.Exists(EntryPath))
            File.Delete(EntryPath);
    }

    public static void Sync(bool enabled)
    {
        if (enabled == IsEnabled)
            return;
        if (enabled)
            Enable();
        else
            Disable();
    }

    private static string[] GetLaunchArguments()
    {
        var assembly = typeof(AutostartService).Assembly.Location;
        var processPath = Environment.ProcessPath ??
            throw new InvalidOperationException("The current process executable could not be resolved.");
        var executableName = Path.GetFileNameWithoutExtension(processPath);
        return executableName.Equals("dotnet", StringComparison.OrdinalIgnoreCase)
            ? [processPath, assembly, "--minimized"]
            : [processPath, "--minimized"];
    }

    private static string BuildWindowsCommand()
    {
        var arguments = GetLaunchArguments();
        var command = string.Join(" ", arguments.Select(QuoteWindowsArgument));
        return $"@echo off\r\nstart \"\" {command}\r\n";
    }

    private static string BuildDesktopEntry()
    {
        var command = string.Join(" ", GetLaunchArguments().Select(QuoteDesktopArgument));
        return $"[Desktop Entry]\nType=Application\nName=Wallflow\nComment=Live wallpaper engine\nExec={command}\nIcon=wallflow\nTerminal=false\nCategories=Utility;\nX-GNOME-Autostart-enabled=true\n";
    }

    private static string BuildMacLaunchAgent()
    {
        var arguments = string.Concat(GetLaunchArguments().Select(argument => $"<string>{XmlEncode(argument)}</string>"));
        return $"""
                <?xml version="1.0" encoding="UTF-8"?>
                <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
                <plist version="1.0">
                <dict>
                  <key>Label</key><string>org.wallflow.app</string>
                  <key>ProgramArguments</key><array>{arguments}</array>
                  <key>RunAtLoad</key><true/>
                  <key>ProcessType</key><string>Background</string>
                </dict>
                </plist>
                """;
    }

    private static string QuoteWindowsArgument(string argument) =>
        $"\"{argument.Replace("\"", "\\\"")}\"";

    private static string QuoteDesktopArgument(string argument) =>
        $"\"{argument.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("$", "\\$").Replace("`", "\\`")}\"";

    private static string XmlEncode(string value) => SecurityElement.Escape(value) ?? "";
}

public sealed class SingleInstanceGuard : IDisposable
{
    private static readonly string InstanceName = CreateInstanceName();
    private readonly Mutex _mutex;
    private readonly CancellationTokenSource _quitListenerCancellation = new();
    private bool _ownsMutex;
    private Task? _quitListener;

    public SingleInstanceGuard()
    {
        _mutex = new Mutex(initiallyOwned: true, $"Wallflow-{InstanceName}", out _ownsMutex);
    }

    public bool IsPrimaryInstance => _ownsMutex;

    public void StartQuitListener(Action requestQuit)
    {
        ArgumentNullException.ThrowIfNull(requestQuit);
        if (!_ownsMutex)
            throw new InvalidOperationException("Only the primary Wallflow instance can accept quit requests.");
        if (_quitListener is not null)
            return;
        _quitListener = ListenForQuitAsync(requestQuit, _quitListenerCancellation.Token);
    }

    public static bool RequestQuit()
    {
        try
        {
            using var client = new NamedPipeClientStream(".", $"Wallflow-{InstanceName}", PipeDirection.Out);
            client.Connect(3000);
            using var writer = new StreamWriter(client, Encoding.UTF8, leaveOpen: true);
            writer.WriteLine("quit");
            writer.Flush();
            return true;
        }
        catch (Exception exception) when (
            exception is IOException or TimeoutException or UnauthorizedAccessException)
        {
            return false;
        }
    }

    public void Dispose()
    {
        _quitListenerCancellation.Cancel();
        if (_ownsMutex)
        {
            _mutex.ReleaseMutex();
            _ownsMutex = false;
        }
        _mutex.Dispose();
        _quitListenerCancellation.Dispose();
    }

    private static async Task ListenForQuitAsync(Action requestQuit, CancellationToken cancellationToken)
    {
        while (!cancellationToken.IsCancellationRequested)
        {
            try
            {
                await using var server = new NamedPipeServerStream(
                    $"Wallflow-{InstanceName}",
                    PipeDirection.In,
                    1,
                    PipeTransmissionMode.Byte,
                    PipeOptions.Asynchronous);
                await server.WaitForConnectionAsync(cancellationToken);
                using var reader = new StreamReader(server, Encoding.UTF8, leaveOpen: true);
                if (await reader.ReadLineAsync(cancellationToken) == "quit")
                    requestQuit();
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                return;
            }
            catch (IOException)
            {
                if (!cancellationToken.IsCancellationRequested)
                    await Task.Delay(100, cancellationToken);
            }
        }
    }

    private static string CreateInstanceName()
    {
        var identity = OperatingSystem.IsWindows()
            ? WindowsIdentity.GetCurrent().User?.Value ?? Environment.UserName
            : Environment.UserName;
        return Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(
            Encoding.UTF8.GetBytes(identity)))[..16];
    }
}

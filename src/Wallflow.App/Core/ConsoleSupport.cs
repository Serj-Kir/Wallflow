using System.Runtime.InteropServices;

namespace Wallflow.App.Core;

/// <summary>
/// The application is a GUI-subsystem executable on Windows, so it has no console
/// of its own. When it is started from an existing terminal (for <c>--version</c>,
/// <c>--list-monitors</c>, <c>--set</c> and friends) this re-attaches to the parent
/// console so the CLI output is visible, without ever opening a new window.
/// </summary>
internal static class ConsoleSupport
{
    private const uint AttachParentProcess = 0xFFFFFFFF;

    public static void AttachToParentConsole()
    {
        if (!OperatingSystem.IsWindows())
            return;
        if (!AttachConsole(AttachParentProcess))
            return;

        try
        {
            Console.SetOut(new StreamWriter(Console.OpenStandardOutput()) { AutoFlush = true });
            Console.SetError(new StreamWriter(Console.OpenStandardError()) { AutoFlush = true });
        }
        catch (IOException)
        {
            // Without a usable handle the application simply keeps its silent writers.
        }
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool AttachConsole(uint processId);
}

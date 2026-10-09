using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Text.Json.Serialization;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Logging;
using Avalonia.Rendering.Composition;
using Avalonia.Threading;
using Avalonia.Vulkan;
using SkiaSharp;

namespace AvaloniaNativeAotSmoke;

internal static class Program
{
    internal static readonly SmokeResult Result = new();
    private static TextWriter _resultOutput = TextWriter.Null;
    private static readonly object ReportLock = new();
    private static bool _reported;

    [STAThread]
    public static int Main(string[] args)
    {
        // Third-party managed diagnostics must not contaminate the JSON stream.
        _resultOutput = Console.Out;
        Console.SetOut(Console.Error);
        using var watchdog = new Timer(_ =>
        {
            if (WriteResult(124, "Timed out waiting for a verified, submitted frame."))
                Environment.Exit(124);
        });
        try
        {
            string? mode = Environment.GetEnvironmentVariable("HOSTFORGE_SMOKE_MODE");
            int timeoutSeconds = 30;
            for (int i = 0; i < args.Length; i++)
            {
                if (args[i] == "--mode" && i + 1 < args.Length)
                    mode = args[++i];
                else if (args[i] == "--timeout-seconds" && i + 1 < args.Length && int.TryParse(args[++i], out int seconds) && seconds is > 0 and <= 300)
                    timeoutSeconds = seconds;
                else
                    throw new ArgumentException("Usage: --mode <single platform mode> [--timeout-seconds <1..300>]");
            }

            Result.Mode = mode ?? throw new ArgumentException("Specify --mode or HOSTFORGE_SMOKE_MODE; there is no default renderer.");
            Result.ExpectedBackend = mode switch
            {
                "Vulkan" => "Vulkan",
                "Metal" => "Metal",
                "AngleEgl" or "Wgl" or "Egl" or "Glx" or "OpenGl" => "OpenGL",
                "Software" => "Software",
                _ => throw new ArgumentException($"Unknown rendering mode: {mode}")
            };
            watchdog.Change(TimeSpan.FromSeconds(timeoutSeconds), Timeout.InfiniteTimeSpan);
            foreach (var metadata in typeof(Program).Assembly.GetCustomAttributes<AssemblyMetadataAttribute>())
            {
                if (metadata.Key == "SkiaPackageVersion") Result.SkiaPackage = metadata.Value;
                if (metadata.Key == "HarfBuzzPackageVersion") Result.HarfBuzzPackage = metadata.Value;
                if (metadata.Key == "RuntimeIdentifier") Result.Rid = metadata.Value;
            }
            Result.SkiaManaged = typeof(SKCanvas).Assembly.GetName().Version?.ToString();
            Result.HarfBuzzManaged = typeof(HarfBuzzSharp.Buffer).Assembly.GetName().Version?.ToString();
            Result.SkiaNative = SkiaSharpVersion.Native.ToString();
            Result.HarfBuzzNative = Marshal.PtrToStringUTF8(HarfBuzzNative.GetVersion());

            Logger.Sink = new StderrLogSink();
            var builder = AppBuilder.Configure<SmokeApplication>().UseSkia().UseHarfBuzz();
            if (mode == "Vulkan")
            {
                var options = new VulkanOptions();
                if (Environment.GetEnvironmentVariable("HOSTFORGE_SMOKE_VULKAN_VERSION") is { Length: > 0 } apiVersion)
                    options.VulkanInstanceCreationOptions.VulkanVersion = Version.Parse(apiVersion);
                Result.RequestedVulkanApiVersion = options.VulkanInstanceCreationOptions.VulkanVersion.ToString();
                builder.With(options);
            }
            if (OperatingSystem.IsWindows())
            {
                var options = new Win32PlatformOptions { RenderingMode = [ParseMode<Win32RenderingMode>(mode)] };
                if (mode == "AngleEgl" && Environment.GetEnvironmentVariable("HOSTFORGE_SMOKE_ANGLE_ADAPTER") is { Length: > 0 } adapter)
                {
                    options.GraphicsAdapterSelectionCallback = adapters =>
                    {
                        for (int index = 0; index < adapters.Count; index++)
                        {
                            if (adapters[index].Description?.Contains(adapter, StringComparison.OrdinalIgnoreCase) == true)
                                return index;
                        }
                        throw new InvalidOperationException($"Requested ANGLE adapter '{adapter}' is unavailable.");
                    };
                }
                builder.UseWin32().With(options);
            }
            else if (OperatingSystem.IsLinux())
                builder.UseX11().With(new X11PlatformOptions
                {
                    RenderingMode = [ParseMode<X11RenderingMode>(mode)],
                    // Software GL implementations are intentional in the Mesa acceptance lane.
                    GlxRendererBlacklist = []
                });
            else if (OperatingSystem.IsMacOS())
                builder.UseAvaloniaNative().With(new AvaloniaNativePlatformOptions { RenderingMode = [ParseMode<AvaloniaNativeRenderingMode>(mode)] });
            else
                throw new PlatformNotSupportedException();

            builder.StartWithClassicDesktopLifetime([], ShutdownMode.OnExplicitShutdown);
            if (!Result.FrameSubmitted && Result.Error is null)
                Result.Error = "Application exited before a verified frame was submitted.";
        }
        catch (Exception exception)
        {
            Result.Error = exception.ToString();
        }

        WriteResult(Result.Error is null && Result.FrameSubmitted && Result.PixelsVerified ? 0 : 1);
        return Result.ExitCode;
    }

    private static T ParseMode<T>(string mode) where T : struct, Enum =>
        Enum.TryParse<T>(mode, out var parsed) && Enum.IsDefined(parsed)
            ? parsed : throw new ArgumentException($"Mode {mode} is not supported by {typeof(T).Name}.");

    private static bool WriteResult(int exitCode, string? error = null)
    {
        lock (ReportLock)
        {
            if (_reported)
                return false;
            _reported = true;
            Result.ExitCode = exitCode;
            Result.Success = exitCode == 0;
            if (error is not null)
                Result.Error = error;
            string json = JsonSerializer.Serialize(Result, SmokeJsonContext.Default.SmokeResult);
            _resultOutput.WriteLine(json);
            _resultOutput.Flush();
            return true;
        }
    }
}

internal sealed class SmokeApplication : Application
{
    public override void OnFrameworkInitializationCompleted()
    {
        if (ApplicationLifetime is not IClassicDesktopStyleApplicationLifetime lifetime)
            throw new InvalidOperationException("A real desktop lifetime is required.");
        var probe = new ProbeControl();
        var window = new Window
        {
            Title = "HostForge actual renderer probe",
            Width = 320,
            Height = 240,
            Content = probe
        };
        lifetime.MainWindow = window;
        window.Closed += (_, _) => lifetime.Shutdown();
        window.Opened += async (_, _) =>
        {
            try
            {
                await probe.Drawn;
                // This barrier is enqueued only after the draw operation has read back its pixels.
                // Rendered completes after render targets have been rendered/disposed (submission).
                var compositor = ElementComposition.GetElementVisual(probe)?.Compositor
                    ?? throw new InvalidOperationException("No window compositor.");
                await compositor.RequestCompositionBatchCommitAsync().Rendered;
                Program.Result.FrameSubmitted = true;
            }
            catch (Exception exception)
            {
                Program.Result.Error = exception.ToString();
            }
            finally
            {
                Dispatcher.UIThread.Post(() => lifetime.Shutdown());
            }
        };
        base.OnFrameworkInitializationCompleted();
    }
}

internal sealed class SmokeResult
{
    public int SchemaVersion { get; set; } = 1;
    public bool Success { get; set; }
    public int ExitCode { get; set; }
    public string? Mode { get; set; }
    public string? ExpectedBackend { get; set; }
    public string? RequestedVulkanApiVersion { get; set; }
    public string? ActualBackend { get; set; }
    public string? Renderer { get; set; }
    public string? Vendor { get; set; }
    public string? GlVersion { get; set; }
    public string? PlatformGraphicsContext { get; set; }
    public bool PixelsVerified { get; set; }
    public string[]? Pixels { get; set; }
    public bool FrameSubmitted { get; set; }
    public string? SkiaPackage { get; set; }
    public string? SkiaManaged { get; set; }
    public string? SkiaNative { get; set; }
    public string? HarfBuzzPackage { get; set; }
    public string? HarfBuzzManaged { get; set; }
    public string? HarfBuzzNative { get; set; }
    public string? Rid { get; set; }
    public string ProcessArchitecture { get; set; } = RuntimeInformation.ProcessArchitecture.ToString();
    public string OperatingSystem { get; set; } = RuntimeInformation.OSDescription;
    public bool NativeAot { get; set; } = !RuntimeFeature.IsDynamicCodeSupported;
    public string? Error { get; set; }
}

[JsonSerializable(typeof(SmokeResult))]
internal partial class SmokeJsonContext : JsonSerializerContext;

internal static class HarfBuzzNative
{
    [DllImport("libHarfBuzzSharp", EntryPoint = "hb_version_string")]
    internal static extern nint GetVersion();
}

internal sealed class StderrLogSink : ILogSink
{
    private int _renderFailed;
    public bool IsEnabled(LogEventLevel level, string area) => level >= LogEventLevel.Warning;
    public void Log(LogEventLevel level, string area, object? source, string messageTemplate) => Console.Error.WriteLine($"[{level}] {area}: {messageTemplate}");
    public void Log(LogEventLevel level, string area, object? source, string messageTemplate, params object?[] propertyValues)
    {
        string message = $"[{level}] {area}: {messageTemplate} {string.Join(", ", propertyValues)}";
        if (level == LogEventLevel.Error && area == "Visual")
        {
            if (Interlocked.Exchange(ref _renderFailed, 1) != 0)
                return;
            Program.Result.Error = message;
            if (Application.Current?.ApplicationLifetime is IClassicDesktopStyleApplicationLifetime lifetime)
                Dispatcher.UIThread.Post(() => lifetime.Shutdown());
        }
        Console.Error.WriteLine(message.TrimEnd('\0'));
    }
}

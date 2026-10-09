using System.Runtime.InteropServices;
using System.Text.Json;
using HostForge.TestInfra;

namespace HostForge.NativeAot.Tests;

public class AvaloniaSmokeTests
{
    [Test]
    [NotInParallel]
    public async Task SelectedRenderers_DrawReadBackAndSubmit()
    {
        string? selection = Environment.GetEnvironmentVariable("HOSTFORGE_SMOKE_MODES");
        RequireEnvironment(!string.IsNullOrWhiteSpace(selection), "Set HOSTFORGE_SMOKE_MODES to the comma-separated modes to exercise.");
        string rid = Environment.GetEnvironmentVariable("HOSTFORGE_SMOKE_RID") ?? RuntimeInformation.RuntimeIdentifier;
        RequireEnvironment(rid == RuntimeInformation.RuntimeIdentifier, $"Selected RID {rid} requires a matching native host; current RID is {RuntimeInformation.RuntimeIdentifier}.");
        if (OperatingSystem.IsLinux())
            RequireEnvironment(!string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("DISPLAY")), "X11 smoke requires DISPLAY (for example xvfb-run).");

        string[] modes = (selection ?? "").Split(',', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries);
        string[] allowed = OperatingSystem.IsWindows() ? ["AngleEgl", "Vulkan", "Wgl", "Software"]
            : OperatingSystem.IsLinux() ? ["Vulkan", "Egl", "Glx", "Software"]
            : OperatingSystem.IsMacOS() ? ["Metal", "OpenGl", "Software"] : [];
        if (modes.Length == 0 || modes.Distinct().Count() != modes.Length || modes.Except(allowed).Any())
            throw new ArgumentException($"Invalid smoke modes for {rid}: {selection}");

        string versionSelection = Environment.GetEnvironmentVariable("HOSTFORGE_SMOKE_SKIA_VERSION") ?? "all";
        string[] versions = versionSelection == "all" ? ["3.119.4", "4.153.1"] : [versionSelection];
        if (versions.Any(version => version is not ("3.119.4" or "4.153.1")))
            throw new ArgumentException($"Unsupported Skia version: {versionSelection}");

        string project = Path.Combine(RepoContext.RepoRoot, "samples", "avalonia-nativeaot-smoke", "AvaloniaNativeAotSmoke.csproj");
        var failures = new List<Exception>();
        foreach (string version in versions)
        {
            string output = Path.Combine(RepoContext.RepoRoot, "artifacts", "avalonia-smoke", version, rid);
            Directory.CreateDirectory(output);
            string arguments = $"publish \"{project}\" -c Release -r {rid} -p:SkiaSharpVersion={version} -p:IlcMaxParallelism=4 -o \"{output}\" -v:minimal";
            foreach (string property in new[] { "SmokeUseStaticSkia", "SmokeSkiaPackageId", "SmokeSkiaPackageVersion", "SmokeUseStaticAngle", "SmokeAnglePackageId", "SmokeAnglePackageVersion", "SmokeUseStaticAvaloniaNative", "SmokeAvaloniaNativePackageId", "SmokeAvaloniaNativePackageVersion", "SmokePackageSource" })
            {
                if (Environment.GetEnvironmentVariable(property) is { Length: > 0 } value)
                    arguments += $" \"-p:{property}={value}\"";
            }

            try
            {
                using var publishTimeout = new CancellationTokenSource(TimeSpan.FromMinutes(10));
                CommandResult publish = await CommandRunner.RunAsync("dotnet", arguments, RepoContext.RepoRoot, publishTimeout.Token);
                await File.WriteAllTextAsync(Path.Combine(output, "publish.log"), publish.CombinedOutput);
                AssertEx.Success(publish, $"Avalonia NativeAOT publish {rid}/{version}");
                AssertEx.FileMissing(Path.Combine(output, "AvaloniaNativeAotSmoke.dll"));
                CheckStaticAssets(output);
            }
            catch (Exception exception)
            {
                failures.Add(exception);
                continue;
            }

            string executable = Path.Combine(output, OperatingSystem.IsWindows() ? "AvaloniaNativeAotSmoke.exe" : "AvaloniaNativeAotSmoke");
            foreach (string mode in modes)
            {
                try
                {
                    using var runTimeout = new CancellationTokenSource(TimeSpan.FromSeconds(60));
                    CommandResult run = await CommandRunner.RunAsync(executable, $"--mode {mode} --timeout-seconds 30", output, runTimeout.Token);
                    await File.WriteAllTextAsync(Path.Combine(output, $"{mode}.json"), run.StandardOutput);
                    await File.WriteAllTextAsync(Path.Combine(output, $"{mode}.stderr.log"), run.StandardError);
                    Console.WriteLine(run.CombinedOutput);
                    AssertEx.Success(run, $"Actual renderer {rid}/{version}/{mode}");
                    using var json = JsonDocument.Parse(run.StandardOutput);
                    JsonElement result = json.RootElement;
                    string expected = mode switch { "Vulkan" => "Vulkan", "Metal" => "Metal", "Software" => "Software", _ => "OpenGL" };
                    await Assert.That(result.GetProperty("Success").GetBoolean()).IsTrue();
                    await Assert.That(result.GetProperty("ExitCode").GetInt32()).IsEqualTo(0);
                    await Assert.That(result.GetProperty("NativeAot").GetBoolean()).IsTrue();
                    await Assert.That(result.GetProperty("Mode").GetString()).IsEqualTo(mode);
                    await Assert.That(result.GetProperty("ActualBackend").GetString()).IsEqualTo(expected);
                    if (mode == "Vulkan")
                        await Assert.That(result.GetProperty("RequestedVulkanApiVersion").GetString()).IsEqualTo(Environment.GetEnvironmentVariable("HOSTFORGE_SMOKE_VULKAN_VERSION") ?? "1.1.0");
                    await Assert.That(result.GetProperty("PixelsVerified").GetBoolean()).IsTrue();
                    await Assert.That(result.GetProperty("FrameSubmitted").GetBoolean()).IsTrue();
                    await Assert.That(result.GetProperty("SkiaPackage").GetString()).IsEqualTo(version);
                    await Assert.That(result.GetProperty("SkiaManaged").GetString()).IsEqualTo(version == "3.119.4" ? "3.119.0.0" : "4.153.0.0");
                    await Assert.That(result.GetProperty("SkiaNative").GetString()).IsEqualTo(version == "3.119.4" ? "119.0" : "153.0");
                    await Assert.That(result.GetProperty("HarfBuzzPackage").GetString()).IsEqualTo(version == "3.119.4" ? "8.3.1.5" : "14.2.1.301");
                    await Assert.That(result.GetProperty("HarfBuzzNative").GetString()).IsEqualTo(version == "3.119.4" ? "8.3.1" : "14.2.1");
                    await Assert.That(result.GetProperty("Rid").GetString()).IsEqualTo(rid);
                    await Assert.That(result.GetProperty("ProcessArchitecture").GetString()).IsEqualTo(RuntimeInformation.ProcessArchitecture.ToString());
                    string[] pixels = result.GetProperty("Pixels").EnumerateArray().Select(pixel => pixel.GetString() ?? "").ToArray();
                    await Assert.That(pixels.SequenceEqual(["#ffff0000", "#ff00ff00", "#ff0000ff", "#ffffffff"])).IsTrue();
                    if (mode == "AngleEgl")
                        await Assert.That(result.GetProperty("Renderer").GetString() ?? "").Contains("ANGLE");
                }
                catch (Exception exception)
                {
                    failures.Add(exception);
                }
            }
        }
        if (failures.Count > 0)
            throw new AggregateException("One or more actual renderer checks failed. See artifacts/avalonia-smoke.", failures);
    }

    private static void RequireEnvironment(bool condition, string reason)
    {
        if (condition)
            return;
        if (Environment.GetEnvironmentVariable("HOSTFORGE_SMOKE_REQUIRED") == "1")
            throw new InvalidOperationException(reason);
        Skip.Test(reason);
    }

    private static void CheckStaticAssets(string output)
    {
        string extension = OperatingSystem.IsWindows() ? ".dll" : OperatingSystem.IsMacOS() ? ".dylib" : ".so";
        var forbidden = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        if (IsEnabled("SmokeUseStaticSkia"))
        {
            forbidden.Add("libSkiaSharp" + extension);
            forbidden.Add("libHarfBuzzSharp" + extension);
        }
        if (IsEnabled("SmokeUseStaticAngle"))
            forbidden.Add("av_libGLESv2.dll");
        if (IsEnabled("SmokeUseStaticAvaloniaNative"))
            forbidden.Add("libAvaloniaNative.dylib");
        if (forbidden.Count == 0)
            return;
        foreach (string path in Directory.EnumerateFiles(output, "*", SearchOption.AllDirectories))
        {
            if (forbidden.Contains(Path.GetFileName(path)))
                throw new InvalidOperationException($"Static smoke published a dynamic library: {path}");
        }
    }

    private static bool IsEnabled(string name) => string.Equals(Environment.GetEnvironmentVariable(name), "true", StringComparison.OrdinalIgnoreCase);
}

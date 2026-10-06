using System.Runtime.InteropServices;
using HostForge.TestInfra;

namespace HostForge.NativeAot.Tests;

public class NativeAotMatrixTests
{
    [Test]
    public async Task CurrentOsX64_PublishesAndRunsWithStaticGraphics()
    {
        if (RuntimeInformation.OSArchitecture != Architecture.X64)
            throw new PlatformNotSupportedException("The NativeAOT matrix requires an x64 host.");

        string rid = OperatingSystem.IsWindows() ? "win-x64"
            : OperatingSystem.IsLinux() ? "linux-x64"
            : throw new PlatformNotSupportedException("The NativeAOT matrix supports Windows and Linux.");

        string project = Path.Combine(
            RepoContext.RepoRoot, "samples", "nativeaot-matrix", "NativeAotMatrix.csproj");
        string output = Path.Combine(RepoContext.ArtifactsTestRoot, $"nativeaot-{rid}-{Guid.NewGuid():N}");
        string arguments = $"publish \"{project}\" -c Release -r {rid} -p:NativeAotMatrixRid={rid} -p:PublishDir=\"{output}\" -v:minimal";
        CommandResult publish = await CommandRunner.RunAsync("dotnet", arguments, RepoContext.RepoRoot);
        AssertEx.Success(publish, $"NativeAOT publish ({rid})");

        string executable = Path.Combine(output, OperatingSystem.IsWindows() ? "NativeAotMatrix.exe" : "NativeAotMatrix");
        AssertEx.FileExists(executable);

        CommandResult run = await CommandRunner.RunAsync(executable, string.Empty, output);
        AssertEx.Success(run, $"NativeAOT run ({rid})");
        AssertEx.Contains(run.CombinedOutput, "SkiaNative=");
        AssertEx.Contains(run.CombinedOutput, "HarfBuzzNative=");
        if (OperatingSystem.IsWindows())
            AssertEx.Contains(run.CombinedOutput, "AngleError=0x");

        string[] dynamicLibraries = OperatingSystem.IsWindows()
            ? ["libSkiaSharp.dll", "libHarfBuzzSharp.dll", "av_libglesv2.dll"]
            : ["libSkiaSharp.so", "libHarfBuzzSharp.so"];
        foreach (string name in dynamicLibraries)
            AssertEx.FileMissing(Path.Combine(output, name));
    }
}

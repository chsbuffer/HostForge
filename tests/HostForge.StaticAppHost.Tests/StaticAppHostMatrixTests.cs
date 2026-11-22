using System.Runtime.InteropServices;
using HostForge.TestInfra;

namespace HostForge.StaticAppHost.Tests;

public class StaticAppHostMatrixTests
{
    private const string LinkAppHostMessageMarker = "[HostForge.StaticAppHost] LinkAppHost executed";
    private const string LinkSingleFileHostMessageMarker = "[HostForge.StaticAppHost] LinkSingleFileHost executed";

    // Every target framework builds the same sample in place, so the runs must not overlap.
    [Test]
    [NotInParallel("samples/simple-pinvoke")]
    [MethodDataSource(typeof(RepoContext), nameof(RepoContext.TargetFrameworkCases))]
    public async Task CurrentOsX64_Matrix_BuildPublish_IncrementalLinkerBehavior(string targetFramework)
    {
        string configuration = "Release";
        string rid = GetCurrentOsX64Rid();

        bool skipExeRun = ReadFlag("HOSTFORGE_MATRIX_SKIP_EXE_RUN");
        bool noClean = ReadFlag("HOSTFORGE_MATRIX_NO_CLEAN");

        string consumerProject = Path.Combine(
            RepoContext.RepoRoot,
            "samples",
            "simple-pinvoke",
            "SimplePInvoke.csproj");

        string consumerDir = Path.GetDirectoryName(consumerProject)!;
        // The sample targets HostLibsTargetFramework, which also selects the matching host libs.
        string properties = $"-c {configuration} -p:HostLibsTargetFramework={targetFramework} -v:minimal";

        if (!noClean)
        {
            DeleteDirectory(Path.Combine(consumerDir, "bin"));
            DeleteDirectory(Path.Combine(consumerDir, "obj"));
        }

        // Kept bin/obj can already hold the links of the first build of each kind, which then relinks only
        // if its inputs changed since the previous run.
        bool? firstBuildLinks = noClean ? null : true;
        string outputDir = Path.Combine(consumerDir, "bin", configuration, targetFramework);
        string executableName = GetPublishedExecutableFileName();
        string noRidExecutable = Path.Combine(outputDir, executableName);
        string ridExecutable = Path.Combine(outputDir, rid, executableName);
        string publishedExecutable = Path.Combine(outputDir, rid, "publish", executableName);

        var steps = new (string Name, string Arguments, bool? ExpectUpToDate, string Executable)[]
        {
            ("01-build-no-rid-first", $"build \"{consumerProject}\" {properties}", firstBuildLinks, noRidExecutable),
            ("02-build-no-rid-second", $"build \"{consumerProject}\" {properties}", false, noRidExecutable),
            ("03-build-rid-first", $"build \"{consumerProject}\" {properties} -r {rid}", firstBuildLinks, ridExecutable),
            ("04-build-rid-second", $"build \"{consumerProject}\" {properties} -r {rid}", false, ridExecutable),
            ("05-publish-first", $"publish \"{consumerProject}\" {properties} -r {rid} /p:PublishSingleFile=true", firstBuildLinks, publishedExecutable),
            ("06-publish-second", $"publish \"{consumerProject}\" {properties} -r {rid} /p:PublishSingleFile=true", false, publishedExecutable)
        };

        foreach ((string name, string arguments, bool? expectedUpToDate, string executable) in steps)
        {
            // Skipping the link is not enough: a host rewritten after its link makes the SDK regenerate and copy it again.
            DateTime? executableWriteTime = expectedUpToDate == false ? File.GetLastWriteTimeUtc(executable) : null;

            CommandResult result = await CommandRunner.RunAsync(
                "dotnet",
                arguments,
                RepoContext.RepoRoot);

            AssertEx.Success(result, $"{targetFramework} {name}");
            AssertEx.FileExists(executable);

            if (executableWriteTime is not null && File.GetLastWriteTimeUtc(executable) != executableWriteTime)
            {
                throw new InvalidOperationException(
                    $"Step {name} ({targetFramework}) rewrote {executable} although nothing changed.{Environment.NewLine}{result.CombinedOutput}");
            }

            if (expectedUpToDate is not null)
            {
                bool actual = ContainsStaticHostLinkMessage(result.CombinedOutput);
                if (actual != expectedUpToDate.Value)
                {
                    string expected = expectedUpToDate.Value ? "HIT" : "MISS";
                    string value = actual ? "HIT" : "MISS";
                    throw new InvalidOperationException(
                        $"Step {name} ({targetFramework}) up-to-date validation failed. Expected {expected}, actual {value}.{Environment.NewLine}{result.CombinedOutput}");
                }
            }
        }

        if (!skipExeRun)
        {
            CommandResult runResult = await CommandRunner.RunAsync(
                publishedExecutable,
                string.Empty,
                Path.GetDirectoryName(publishedExecutable)!);

            AssertEx.Success(runResult, $"{targetFramework} 07-run-exe");
            AssertEx.Contains(runResult.CombinedOutput, "Hello, World!2");
        }

        if (OperatingSystem.IsWindows())
        {
            await AssertWindowsGuardsAsync(consumerProject, properties, Path.Combine(consumerDir, "obj", configuration, targetFramework, rid), targetFramework, rid);
        }
    }

    private static async Task AssertWindowsGuardsAsync(string consumerProject, string properties, string intermediateDirectory, string targetFramework, string rid)
    {
        // The relinked x64 hosts keep the Control Flow Guard and EH continuation table of the official ones.
        string appHost = Path.Combine(intermediateDirectory, "apphost.static.exe");
        foreach (string host in new[] { appHost, Path.Combine(intermediateDirectory, "singlefilehost.static.exe") })
        {
            PeGuardFlags.AssertSet(host, PeGuardFlags.CfInstrumented, "Control Flow Guard");
            PeGuardFlags.AssertSet(host, PeGuardFlags.EHContinuationTablePresent, "the EH continuation table");
        }

        CommandResult result = await CommandRunner.RunAsync(
            "dotnet",
            $"build \"{consumerProject}\" {properties} -r {rid} -p:StaticHostGuardEHCont=false",
            RepoContext.RepoRoot);
        AssertEx.Success(result, $"{targetFramework} 08-build-rid-without-ehcont");
        AssertEx.Contains(result.CombinedOutput, LinkAppHostMessageMarker);
        PeGuardFlags.AssertSet(appHost, PeGuardFlags.CfInstrumented, "Control Flow Guard");
        PeGuardFlags.AssertClear(appHost, PeGuardFlags.EHContinuationTablePresent, "an EH continuation table");
    }

    private static string GetCurrentOsX64Rid()
    {
        if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
        {
            return "win-x64";
        }

        if (RuntimeInformation.IsOSPlatform(OSPlatform.Linux))
        {
            return "linux-x64";
        }

        throw new PlatformNotSupportedException("StaticAppHostMatrixTests currently supports only Windows and Linux runners.");
    }

    private static string GetPublishedExecutableFileName()
    {
        return RuntimeInformation.IsOSPlatform(OSPlatform.Windows)
            ? "SimplePInvoke.exe"
            : "SimplePInvoke";
    }

    private static bool ContainsStaticHostLinkMessage(string output)
    {
        return output.Contains(LinkAppHostMessageMarker, StringComparison.Ordinal) ||
               output.Contains(LinkSingleFileHostMessageMarker, StringComparison.Ordinal);
    }

    private static bool ReadFlag(string name)
    {
        string? value = Environment.GetEnvironmentVariable(name);
        return string.Equals(value, "1", StringComparison.Ordinal) ||
               string.Equals(value, "true", StringComparison.OrdinalIgnoreCase);
    }

    private static void DeleteDirectory(string path)
    {
        if (Directory.Exists(path))
        {
            Directory.Delete(path, recursive: true);
        }
    }
}

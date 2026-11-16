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

        var steps = new (string Name, string Arguments, bool? ExpectUpToDate)[]
        {
            ("01-build-no-rid-first", $"build \"{consumerProject}\" {properties}", true),
            ("02-build-no-rid-second", $"build \"{consumerProject}\" {properties}", false),
            ("03-build-rid-first", $"build \"{consumerProject}\" {properties} -r {rid}", true),
            ("04-build-rid-second", $"build \"{consumerProject}\" {properties} -r {rid}", false),
            ("05-publish-first", $"publish \"{consumerProject}\" {properties} -r {rid} /p:PublishSingleFile=true", true),
            ("06-publish-second", $"publish \"{consumerProject}\" {properties} -r {rid} /p:PublishSingleFile=true", false)
        };

        foreach ((string name, string arguments, bool? expectedUpToDate) in steps)
        {
            CommandResult result = await CommandRunner.RunAsync(
                "dotnet",
                arguments,
                RepoContext.RepoRoot);

            AssertEx.Success(result, $"{targetFramework} {name}");

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
            string executablePath = Path.Combine(
                consumerDir,
                "bin",
                configuration,
                targetFramework,
                rid,
                "publish",
                GetPublishedExecutableFileName());

            AssertEx.FileExists(executablePath);

            CommandResult runResult = await CommandRunner.RunAsync(
                executablePath,
                string.Empty,
                Path.GetDirectoryName(executablePath)!);

            AssertEx.Success(runResult, $"{targetFramework} 07-run-exe");
            AssertEx.Contains(runResult.CombinedOutput, "Hello, World!2");
        }
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

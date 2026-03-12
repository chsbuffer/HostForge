using System.Xml.Linq;
using System.Runtime.InteropServices;
using HostForge.TestInfra;

namespace HostForge.StaticAppHost.Tests;

public class StaticAppHostMatrixTests
{
    private const string LinkAppHostMessageMarker = "[HostForge.StaticAppHost] LinkAppHost executed";
    private const string LinkSingleFileHostMessageMarker = "[HostForge.StaticAppHost] LinkSingleFileHost executed";

    [Test]
    public async Task CurrentOsX64_Matrix_BuildPublish_IncrementalLinkerBehavior()
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
        string targetFramework = ReadTargetFramework(consumerProject);

        if (!noClean)
        {
            DeleteDirectory(Path.Combine(consumerDir, "bin"));
            DeleteDirectory(Path.Combine(consumerDir, "obj"));
        }

        var steps = new (string Name, string Arguments, bool? ExpectUpToDate)[]
        {
            ("01-build-no-rid-first", $"build \"{consumerProject}\" -c {configuration} -v:minimal", true),
            ("02-build-no-rid-second", $"build \"{consumerProject}\" -c {configuration} -v:minimal", false),
            ("03-build-rid-first", $"build \"{consumerProject}\" -c {configuration} -r {rid} -v:minimal", true),
            ("04-build-rid-second", $"build \"{consumerProject}\" -c {configuration} -r {rid} -v:minimal", false),
            ("05-publish-first", $"publish \"{consumerProject}\" -c {configuration} -r {rid} /p:PublishSingleFile=true -v:minimal", true),
            ("06-publish-second", $"publish \"{consumerProject}\" -c {configuration} -r {rid} /p:PublishSingleFile=true -v:minimal", false)
        };

        foreach ((string name, string arguments, bool? expectedUpToDate) in steps)
        {
            CommandResult result = await CommandRunner.RunAsync(
                "dotnet",
                arguments,
                RepoContext.RepoRoot);

            AssertEx.Success(result, name);

            if (expectedUpToDate is not null)
            {
                bool actual = ContainsStaticHostLinkMessage(result.CombinedOutput);
                if (actual != expectedUpToDate.Value)
                {
                    string expected = expectedUpToDate.Value ? "HIT" : "MISS";
                    string value = actual ? "HIT" : "MISS";
                    throw new InvalidOperationException(
                        $"Step {name} up-to-date validation failed. Expected {expected}, actual {value}.{Environment.NewLine}{result.CombinedOutput}");
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

            AssertEx.Success(runResult, "07-run-exe");
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

    private static string ReadTargetFramework(string projectPath)
    {
        var document = XDocument.Load(projectPath);
        string? tfm = document.Root?
            .Descendants()
            .FirstOrDefault(x => x.Name.LocalName == "TargetFramework")
            ?.Value
            .Trim();

        if (string.IsNullOrWhiteSpace(tfm))
        {
            throw new InvalidOperationException($"Unable to resolve TargetFramework from {projectPath}");
        }

        return tfm;
    }

    private static void DeleteDirectory(string path)
    {
        if (Directory.Exists(path))
        {
            Directory.Delete(path, recursive: true);
        }
    }
}

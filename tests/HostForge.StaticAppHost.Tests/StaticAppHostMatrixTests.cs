using System.Xml.Linq;
using HostForge.TestInfra;

namespace HostForge.StaticAppHost.Tests;

public class StaticAppHostMatrixTests
{
    [Test]
    public async Task WinX64_Matrix_BuildPublish_IncrementalLinkerBehavior()
    {
        string configuration = "Release";
        string rid = "win-x64";

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

        var steps = new (string Name, string Arguments, bool? ExpectIncrementalLinker)[]
        {
            ("01-build-no-r-first", $"build \"{consumerProject}\" -c {configuration} -v:minimal", true),
            ("02-build-no-r-second", $"build \"{consumerProject}\" -c {configuration} -v:minimal", false),
            ("03-build-r-first", $"build \"{consumerProject}\" -c {configuration} -r {rid} -v:minimal", true),
            ("04-build-r-second", $"build \"{consumerProject}\" -c {configuration} -r {rid} -v:minimal", false),
            ("05-publish-first", $"publish \"{consumerProject}\" -c {configuration} -r {rid} /p:PublishSingleFile=true -v:minimal", true),
            ("06-publish-second", $"publish \"{consumerProject}\" -c {configuration} -r {rid} /p:PublishSingleFile=true -v:minimal", false)
        };

        foreach ((string name, string arguments, bool? expectedIncrementalLinker) in steps)
        {
            CommandResult result = await CommandRunner.RunAsync(
                "dotnet",
                arguments,
                RepoContext.RepoRoot);

            AssertEx.Success(result, name);

            if (expectedIncrementalLinker is not null)
            {
                bool actual = result.CombinedOutput.Contains("Incremental Linker", StringComparison.Ordinal);
                if (actual != expectedIncrementalLinker.Value)
                {
                    string expected = expectedIncrementalLinker.Value ? "HIT" : "MISS";
                    string value = actual ? "HIT" : "MISS";
                    throw new InvalidOperationException(
                        $"Step {name} incremental linker validation failed. Expected {expected}, actual {value}.{Environment.NewLine}{result.CombinedOutput}");
                }
            }
        }

        if (!skipExeRun)
        {
            string exePath = Path.Combine(
                consumerDir,
                "bin",
                configuration,
                targetFramework,
                rid,
                "publish",
                "SimplePInvoke.exe");

            AssertEx.FileExists(exePath);

            CommandResult runResult = await CommandRunner.RunAsync(
                exePath,
                string.Empty,
                Path.GetDirectoryName(exePath)!);

            AssertEx.Success(runResult, "07-run-exe");
        }
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

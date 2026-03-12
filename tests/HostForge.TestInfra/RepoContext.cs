using Microsoft.Build.Evaluation;
using Microsoft.Build.Locator;
using System.Runtime.CompilerServices;

namespace HostForge.TestInfra;

static class InitMsBuild {
#pragma warning disable CA2255
    [ModuleInitializer]
#pragma warning restore CA2255
    public static void AssemblyInitialize(){
        MSBuildLocator.RegisterDefaults();
    }
}

public static class RepoContext
{
    public const string TargetAvaloniaVersionEnvironmentVariableName = "TargetAvaloniaVersion";
    public const string AvaloniaAppHostBasePackageId = "ChsBuffer.Avalonia.AppHost";

    public static string RepoRoot { get; } = LocateRepoRoot();

    public static string? TargetAvaloniaVersion { get; } = ReadOptionalEnvironmentVariable(TargetAvaloniaVersionEnvironmentVariableName);

    public static string AvaloniaPackageVersion { get; } = ReadProperty("AvaloniaAppHostPackageVersion");

    public static string AvaloniaPackageIdentityVersion { get; } = StripSemVerBuildMetadata(AvaloniaPackageVersion);

    public static string SkiaSharpVersion { get; } = ReadProperty("SkiaSharpVersion");

    public static string HarfBuzzVersion { get; } = ReadProperty("HarfBuzzVersion");

    public static string HostLibsVersion { get; } = ReadProperty("HostLibsVersion");

    public static string AvaloniaPackageOutputDir { get; } =
        Path.Combine(RepoRoot, "artifacts", "packages", "Release");

    public static string ArtifactsTestRoot { get; } =
        Path.Combine(RepoRoot, "artifacts", "tmp", "build-tests");

    public static string AppendTargetAvaloniaVersionProperty(string arguments)
    {
        if (string.IsNullOrWhiteSpace(TargetAvaloniaVersion))
        {
            return arguments;
        }

        return $"{arguments} -p:TargetAvaloniaVersion={TargetAvaloniaVersion}";
    }

    public static string GetAvaloniaPackageId(string packageMode)
    {
        return packageMode switch
        {
            "all" => AvaloniaAppHostBasePackageId,
            "windows" => $"{AvaloniaAppHostBasePackageId}.Windows",
            "linux" => $"{AvaloniaAppHostBasePackageId}.Linux",
            _ => throw new InvalidOperationException(
                $"Unsupported Avalonia package mode '{packageMode}'. Expected 'windows', 'linux', or 'all'.")
        };
    }

    private static string LocateRepoRoot()
    {
        var current = new DirectoryInfo(AppContext.BaseDirectory);

        while (current is not null)
        {
            if (Directory.Exists(Path.Combine(current.FullName, ".git")))
            {
                return current.FullName;
            }

            current = current.Parent;
        }

        throw new InvalidOperationException("Unable to locate repository root from test process.");
    }

    private static string ReadProperty(string propertyName)
    {
        string propsFile = Path.Combine(RepoRoot, "Directory.Build.props");
        string? value;

        using (var projectCollection = new ProjectCollection())
        {
            var project = new Project(
                propsFile,
                globalProperties: BuildGlobalProperties(),
                toolsVersion: null,
                projectCollection: projectCollection,
                loadSettings: ProjectLoadSettings.IgnoreMissingImports);

            value = project.GetPropertyValue(propertyName);
        }

        if (string.IsNullOrWhiteSpace(value))
        {
            throw new InvalidOperationException($"Property '{propertyName}' was not found in {propsFile}.");
        }

        return value;
    }

    private static Dictionary<string, string>? BuildGlobalProperties()
    {
        if (string.IsNullOrWhiteSpace(TargetAvaloniaVersion))
        {
            return null;
        }

        return new Dictionary<string, string>(StringComparer.Ordinal)
        {
            [TargetAvaloniaVersionEnvironmentVariableName] = TargetAvaloniaVersion
        };
    }

    private static string? ReadOptionalEnvironmentVariable(string name)
    {
        string? value = Environment.GetEnvironmentVariable(name);
        return string.IsNullOrWhiteSpace(value) ? null : value.Trim();
    }

    private static string StripSemVerBuildMetadata(string version)
    {
        int separatorIndex = version.IndexOf('+', StringComparison.Ordinal);
        return separatorIndex < 0 ? version : version[..separatorIndex];
    }
}

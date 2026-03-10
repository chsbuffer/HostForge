using Microsoft.Build.Evaluation;
using Microsoft.Build.Locator;

namespace HostForge.TestInfra;

public static class RepoContext
{
    public static string RepoRoot { get; } = LocateRepoRoot();

    public static string AvaloniaPackageVersion { get; } = ReadProperty("AvaloniaAppHostPackageVersion");

    public static string HostLibsVersion { get; } = ReadProperty("HostLibsVersion");

    public static string AvaloniaPackageOutputDir { get; } =
        Path.Combine(RepoRoot, "artifacts", "packages", "Release");

    public static string ArtifactsTestRoot { get; } =
        Path.Combine(RepoRoot, "artifacts", "tmp", "build-tests");

    static RepoContext()
    {
        MSBuildLocator.RegisterDefaults();
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
                globalProperties: null, // TODO: Read TargetAvaloniaVersion from environment
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
}

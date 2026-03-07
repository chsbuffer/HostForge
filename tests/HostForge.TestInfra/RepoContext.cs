using System.Xml.Linq;

namespace HostForge.TestInfra;

public static class RepoContext
{
    public static string RepoRoot { get; } = LocateRepoRoot();

    public static string AvaloniaPackageVersion { get; } = ReadProperty("AvaloniaAppHostPackageVersion");

    public static string AvaloniaPackageOutputDir { get; } =
        Path.Combine(RepoRoot, "artifacts", "packages", "Release");

    public static string ArtifactsTestRoot { get; } =
        Path.Combine(RepoRoot, "artifacts", "tmp", "build-tests");

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
        var document = XDocument.Load(propsFile);
        string? value = document.Root?
            .Descendants()
            .FirstOrDefault(x => x.Name.LocalName == propertyName)?
            .Value
            .Trim();

        if (string.IsNullOrWhiteSpace(value))
        {
            throw new InvalidOperationException($"Property '{propertyName}' was not found in {propsFile}.");
        }

        return value;
    }
}

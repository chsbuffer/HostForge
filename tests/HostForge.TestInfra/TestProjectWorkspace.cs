using System.Text;

namespace HostForge.TestInfra;

public sealed class TestProjectWorkspace : IAsyncDisposable
{
    private readonly string _rootDirectory;

    public string ProjectDirectory { get; }
    public string ProjectFilePath { get; }
    public string ProjectName { get; }

    private TestProjectWorkspace(string rootDirectory, string projectDirectory, string projectFilePath, string projectName)
    {
        _rootDirectory = rootDirectory;
        ProjectDirectory = projectDirectory;
        ProjectFilePath = projectFilePath;
        ProjectName = projectName;
    }

    public static async Task<TestProjectWorkspace> CreateAsync(
        string targetFramework,
        string runtimeIdentifier,
        bool includeSkiaPackages,
        bool includeNativeAssetsPackages,
        bool? disableSkiaHarfBuzzRuntimeCopy = null,
        CancellationToken cancellationToken = default)
    {
        string guid = Guid.NewGuid().ToString("N");
        string projectName = $"BuildTest_{targetFramework.Replace('.', '_')}_{runtimeIdentifier.Replace('-', '_')}_{guid[..8]}";
        string rootDirectory = RepoContext.ArtifactsTestRoot;
        string projectDirectory = Path.Combine(rootDirectory, projectName);
        string projectFilePath = Path.Combine(projectDirectory, $"{projectName}.csproj");

        Directory.CreateDirectory(projectDirectory);
        Directory.CreateDirectory(rootDirectory);

        await File.WriteAllTextAsync(
            Path.Combine(projectDirectory, "Program.cs"),
            BuildProgramSource(includeSkiaPackages),
            cancellationToken);

        string csproj = BuildProjectFile(
            projectName,
            targetFramework,
            runtimeIdentifier,
            includeSkiaPackages,
            includeNativeAssetsPackages,
            disableSkiaHarfBuzzRuntimeCopy);

        await File.WriteAllTextAsync(projectFilePath, csproj, cancellationToken);

        return new TestProjectWorkspace(rootDirectory, projectDirectory, projectFilePath, projectName);
    }

    public string GetPublishDirectory(string configuration, string targetFramework, string runtimeIdentifier)
    {
        return Path.Combine(
            ProjectDirectory,
            "bin",
            configuration,
            targetFramework,
            runtimeIdentifier,
            "publish");
    }

    public ValueTask DisposeAsync()
    {
        try
        {
            if (Directory.Exists(ProjectDirectory))
            {
                Directory.Delete(ProjectDirectory, recursive: true);
            }
        }
        catch
        {
            // Keep test cleanup best-effort; artifacts may be useful for diagnosis.
        }

        return ValueTask.CompletedTask;
    }

    // TODO: Create with Microsoft.Build.Construction
    // TODO: Don't hardcoded package versions
    private static string BuildProjectFile(
        string projectName,
        string targetFramework,
        string runtimeIdentifier,
        bool includeSkiaPackages,
        bool includeNativeAssetsPackages,
        bool? disableSkiaHarfBuzzRuntimeCopy)
    {
        var builder = new StringBuilder();
        builder.AppendLine("<Project Sdk=\"Microsoft.NET.Sdk\">");
        builder.AppendLine("  <PropertyGroup>");
        builder.AppendLine("    <OutputType>Exe</OutputType>");
        builder.AppendLine($"    <TargetFramework>{targetFramework}</TargetFramework>");
        builder.AppendLine($"    <RuntimeIdentifier>{runtimeIdentifier}</RuntimeIdentifier>");
        builder.AppendLine("    <ImplicitUsings>enable</ImplicitUsings>");
        builder.AppendLine("    <Nullable>enable</Nullable>");
        builder.AppendLine($"    <RestoreAdditionalProjectSources>$(RestoreAdditionalProjectSources);{RepoContext.AvaloniaPackageOutputDir}</RestoreAdditionalProjectSources>");

        if (disableSkiaHarfBuzzRuntimeCopy is not null)
        {
            builder.AppendLine($"    <DisableSkiaHarfBuzzRuntimeCopy>{disableSkiaHarfBuzzRuntimeCopy.Value.ToString().ToLowerInvariant()}</DisableSkiaHarfBuzzRuntimeCopy>");
        }

        builder.AppendLine("  </PropertyGroup>");
        builder.AppendLine();
        builder.AppendLine("  <ItemGroup>");
        builder.AppendLine($"    <PackageReference Include=\"ChsBuffer.Avalonia.AppHost\" Version=\"{RepoContext.AvaloniaPackageVersion}\" />");

        if (includeSkiaPackages)
        {
            builder.AppendLine("    <PackageReference Include=\"SkiaSharp\" Version=\"2.88.9\" />");
            builder.AppendLine("    <PackageReference Include=\"HarfBuzzSharp\" Version=\"8.3.1.1\" />");
        }

        if (includeNativeAssetsPackages)
        {
            builder.AppendLine("    <PackageReference Include=\"SkiaSharp.NativeAssets.Win32\" Version=\"2.88.9\" />");
            builder.AppendLine("    <PackageReference Include=\"HarfBuzzSharp.NativeAssets.Win32\" Version=\"8.3.1.1\" />");
        }

        builder.AppendLine("  </ItemGroup>");
        builder.AppendLine("</Project>");
        return builder.ToString();
    }

    private static string BuildProgramSource(bool includeSkiaPackages)
    {
        if (includeSkiaPackages)
        {
            return "Console.WriteLine($\"SkiaSharpVersion.Native={SkiaSharp.SkiaSharpVersion.Native}\");";
        }

        return "Console.WriteLine(\"HostForge build test\");";
    }
}

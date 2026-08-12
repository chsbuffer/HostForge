using Microsoft.Build.Construction;

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
        bool includeNativeAssetsPackages,
        bool? disableSkiaHarfBuzzRuntimeCopy = null,
        bool? disableAngleRuntimeCopy = null,
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
            BuildProgramSource(),
            cancellationToken);

        BuildProjectFile(
            projectFilePath,
            targetFramework,
            runtimeIdentifier,
            includeNativeAssetsPackages,
            disableSkiaHarfBuzzRuntimeCopy,
            disableAngleRuntimeCopy);

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

    private static void BuildProjectFile(
        string projectFilePath,
        string targetFramework,
        string runtimeIdentifier,
        bool includeNativeAssetsPackages,
        bool? disableSkiaHarfBuzzRuntimeCopy,
        bool? disableAngleRuntimeCopy)
    {
        ProjectRootElement project = ProjectRootElement.Create();
        project.Sdk = "Microsoft.NET.Sdk";

        ProjectPropertyGroupElement propertyGroup = project.AddPropertyGroup();
        propertyGroup.AddProperty("OutputType", "Exe");
        propertyGroup.AddProperty("TargetFramework", targetFramework);
        propertyGroup.AddProperty("RuntimeIdentifier", runtimeIdentifier);
        propertyGroup.AddProperty("ImplicitUsings", "enable");
        propertyGroup.AddProperty("Nullable", "enable");
        propertyGroup.AddProperty(
            "DefineConstants",
            "$(DefineConstants);HOSTFORGE_DISABLE_AVALONIA_WIN32_IMPORT_RESOLVER");
        propertyGroup.AddProperty(
            "RestoreAdditionalProjectSources",
            "$(RestoreAdditionalProjectSources);$(AvaloniaAppHostPackageOutputDir)");

        propertyGroup.AddProperty("DetectAvaloniaDesigner", "false");

        if (disableSkiaHarfBuzzRuntimeCopy is not null)
        {
            propertyGroup.AddProperty(
                "DisableSkiaHarfBuzzRuntimeCopy",
                disableSkiaHarfBuzzRuntimeCopy.Value.ToString().ToLowerInvariant());
        }

        if (disableAngleRuntimeCopy is not null)
        {
            propertyGroup.AddProperty(
                "DisableAngleRuntimeCopy",
                disableAngleRuntimeCopy.Value.ToString().ToLowerInvariant());
        }

        ProjectItemGroupElement itemGroup = project.AddItemGroup();
        AddPackageReference(itemGroup, RepoContext.GetAvaloniaRidPackageId(runtimeIdentifier), "$(AvaloniaAppHostPackageVersion)");
        AddPackageReference(itemGroup, RepoContext.AvaloniaAppHostBuildPackageId, "$(AvaloniaAppHostPackageVersion)");
        AddPackageReference(itemGroup, "SkiaSharp", "$(SkiaSharpVersion)");
        AddPackageReference(itemGroup, "HarfBuzzSharp", "$(HarfBuzzVersion)");

        if (includeNativeAssetsPackages)
        {
            foreach ((string packageId, string versionProperty) in GetNativeAssetsPackageReferences(runtimeIdentifier))
            {
                AddPackageReference(itemGroup, packageId, versionProperty);
            }
        }

        project.Save(projectFilePath);
    }

    private static void AddPackageReference(ProjectItemGroupElement itemGroup, string include, string version)
    {
        ProjectItemElement packageReference = itemGroup.AddItem("PackageReference", include);
        packageReference.AddMetadata("Version", version, expressAsAttribute: true);
    }

    private static IEnumerable<(string PackageId, string VersionProperty)> GetNativeAssetsPackageReferences(string runtimeIdentifier)
    {
        if (runtimeIdentifier.StartsWith("win", StringComparison.Ordinal))
        {
            yield return ("Avalonia.Angle.Windows.Natives", "$(AngleVersion)");
            yield return ("SkiaSharp.NativeAssets.Win32", "$(SkiaSharpVersion)");
            yield return ("HarfBuzzSharp.NativeAssets.Win32", "$(HarfBuzzVersion)");
            yield break;
        }

        if (runtimeIdentifier.StartsWith("linux", StringComparison.Ordinal))
        {
            yield return ("SkiaSharp.NativeAssets.Linux.NoDependencies", "$(SkiaSharpVersion)");
            yield return ("HarfBuzzSharp.NativeAssets.Linux", "$(HarfBuzzVersion)");
            yield break;
        }

        throw new InvalidOperationException(
            $"Unsupported runtime identifier '{runtimeIdentifier}' for native assets package selection.");
    }

    private static string BuildProgramSource()
    {
        return """
                using System.Runtime.InteropServices;

                if (OperatingSystem.IsWindows())
                {
                #if NET10_0
                    NativeLibrary.SetDllImportResolver(
                        typeof(AngleNative).Assembly,
                        static (libraryName, _, _) =>
                            string.Equals(libraryName, "av_libglesv2.dll", StringComparison.OrdinalIgnoreCase)
                                ? NativeLibrary.GetMainProgramHandle()
                                : IntPtr.Zero);
                #endif
                    Console.WriteLine($"AngleError=0x{AngleNative.GetError():X}");
                }
                Console.WriteLine($"SkiaSharpVersion.Native={SkiaSharp.SkiaSharpVersion.Native}");

                internal static class AngleNative
                {
                    [DllImport("av_libglesv2.dll", EntryPoint = "EGL_GetError")]
                    internal static extern uint GetError();
                }
                """;
    }
}

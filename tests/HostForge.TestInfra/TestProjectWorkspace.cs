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
        bool publishSingleFile = false,
        bool setRuntimeIdentifier = true,
        bool referenceAvaloniaWin32 = false,
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
            disableAngleRuntimeCopy,
            publishSingleFile,
            setRuntimeIdentifier,
            referenceAvaloniaWin32);

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
        bool? disableAngleRuntimeCopy,
        bool publishSingleFile,
        bool setRuntimeIdentifier,
        bool referenceAvaloniaWin32)
    {
        ProjectRootElement project = ProjectRootElement.Create();
        project.Sdk = "Microsoft.NET.Sdk";

        ProjectPropertyGroupElement propertyGroup = project.AddPropertyGroup();
        propertyGroup.AddProperty("OutputType", "Exe");
        propertyGroup.AddProperty("TargetFramework", targetFramework);
        // runtimeIdentifier also selects the RID and native asset packages, which a build without a RID still references.
        if (setRuntimeIdentifier)
            propertyGroup.AddProperty("RuntimeIdentifier", runtimeIdentifier);
        propertyGroup.AddProperty("ImplicitUsings", "enable");
        propertyGroup.AddProperty("Nullable", "enable");
        propertyGroup.AddProperty(
            "RestoreAdditionalProjectSources",
            "$(RestoreAdditionalProjectSources);$(AvaloniaAppHostPackageOutputDir)");

        if (publishSingleFile)
        {
            // Self-contained single-file publishing uses the prelinked singlefilehost template. Pin the
            // managed framework to the template's native runtime, as the package README requires;
            // otherwise the runtime pack follows the latest patch known to the installed SDK.
            propertyGroup.AddProperty("PublishSingleFile", "true");
            propertyGroup.AddProperty("SelfContained", "true");
            propertyGroup.AddProperty("RuntimeFrameworkVersion", RepoContext.GetHostLibsVersion(targetFramework));
        }

        if (referenceAvaloniaWin32)
        {
            // Compiles the call to the ANGLE import of Avalonia.Win32 into the program.
            propertyGroup.AddProperty("DefineConstants", "$(DefineConstants);REFERENCES_AVALONIA_WIN32");
        }

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
        // Skottie also brings SkiaSharp.SceneGraph and SkiaSharp.Resources, each with its own libSkiaSharp imports.
        AddPackageReference(itemGroup, "SkiaSharp.Skottie", "$(SkiaSharpVersion)");
        AddPackageReference(itemGroup, "HarfBuzzSharp", "$(HarfBuzzVersion)");
        if (referenceAvaloniaWin32)
            AddPackageReference(itemGroup, "Avalonia.Win32", "$(AvaloniaVersion)");

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
                using System.Diagnostics;
                using System.Reflection;
                using System.Runtime.InteropServices;

                if (OperatingSystem.IsWindows())
                {
                #if NET10_0_OR_GREATER
                    NativeLibrary.SetDllImportResolver(
                        typeof(AngleNative).Assembly,
                        static (libraryName, _, _) =>
                            string.Equals(libraryName, "av_libglesv2.dll", StringComparison.OrdinalIgnoreCase)
                            && NativeLibrary.TryGetExport(NativeLibrary.GetMainProgramHandle(), "EGL_GetError", out _)
                                ? NativeLibrary.GetMainProgramHandle()
                                : IntPtr.Zero);
                #endif
                    Console.WriteLine($"AngleError=0x{AngleNative.GetError():X}");
                #if REFERENCES_AVALONIA_WIN32
                    // Avalonia.Win32 loads ANGLE through its own EGL_GetProcAddress import, an internal method.
                    MethodInfo eglGetProcAddress = typeof(Avalonia.Win32PlatformOptions).Assembly.GetTypes()
                        .SelectMany(type => type.GetMethods(BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly))
                        .First(method => method.GetCustomAttribute<DllImportAttribute>() is { EntryPoint: "EGL_GetProcAddress" } import
                            && Path.GetFileNameWithoutExtension(import.Value).Equals("av_libglesv2", StringComparison.OrdinalIgnoreCase));
                    IntPtr eglGetError = (IntPtr)eglGetProcAddress.Invoke(null, new object[] { "eglGetError" })!;
                    Console.WriteLine($"AvaloniaWin32Angle={eglGetError != IntPtr.Zero}");
                #endif
                }
                Console.WriteLine($"SkiaSharpVersion.Native={SkiaSharp.SkiaSharpVersion.Native}");
                using (var harfBuzzBuffer = new HarfBuzzSharp.Buffer())
                {
                    harfBuzzBuffer.AddUtf8("HostForge");
                    Console.WriteLine($"HarfBuzzLength={harfBuzzBuffer.Length}");
                }
                using (var resources = new SkiaSharp.Resources.FileResourceProvider(AppContext.BaseDirectory))
                using (var invalidation = new SkiaSharp.SceneGraph.InvalidationController())
                {
                    invalidation.Begin();
                    invalidation.End();
                    bool parsed = SkiaSharp.Skottie.Animation.TryParse(
                        "{\"v\":\"5.7.4\",\"fr\":30,\"ip\":0,\"op\":30,\"w\":16,\"h\":16,\"layers\":[]}", out var animation);
                    animation?.Dispose();
                    Console.WriteLine($"Skottie={parsed}");
                }
                Console.WriteLine($"RuntimeVersion={Environment.Version}");
                string[] graphicsLibraries = ["libSkiaSharp", "libHarfBuzzSharp", "av_libglesv2"];
                IEnumerable<string> loadedGraphicsLibraries = Process.GetCurrentProcess().Modules.Cast<ProcessModule>()
                    .Select(module => Path.GetFileName(module.FileName))
                    .Where(file => graphicsLibraries.Any(library => file.StartsWith(library, StringComparison.OrdinalIgnoreCase)));
                Console.WriteLine($"GraphicsLibraryFiles={string.Join(",", loadedGraphicsLibraries)}");

                internal static class AngleNative
                {
                    [DllImport("av_libglesv2.dll", EntryPoint = "EGL_GetError")]
                    internal static extern uint GetError();
                }
                """;
    }
}

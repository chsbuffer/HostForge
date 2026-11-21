namespace HostForge.TestInfra;

public static class AvaloniaPackageBuilder
{
    private static readonly SemaphoreSlim Lock = new(1, 1);
    private static bool _packed;
    private const string RootfsDirEnvironmentVariableName = "ROOTFS_DIR";

    public static async Task EnsurePackedAsync(CancellationToken cancellationToken = default)
    {
        if (_packed)
        {
            return;
        }

        await Lock.WaitAsync(cancellationToken);
        try
        {
            if (_packed)
            {
                return;
            }

            string ridProject = Path.Combine(
                RepoContext.RepoRoot,
                "src",
                "package-avalonia-apphost",
                "AvaloniaAppHost.Rid.csproj");
            string buildProject = Path.Combine(
                RepoContext.RepoRoot,
                "src",
                "package-avalonia-apphost",
                "AvaloniaAppHost.Build.csproj");
            string packagesCache = Path.Combine(
                RepoContext.RepoRoot,
                "artifacts", "tmp", "nuget", "packages");

            // A RID package contains every template TFM, so pack only RIDs linked for all of them.
            string artifactRoot = Path.Combine(
                RepoContext.RepoRoot,
                "artifacts", "avalonia-host", RepoContext.TargetAvaloniaVersion);
            string[] knownRids = ["win-x64", "win-arm64", "linux-x64", "linux-musl-x64", "linux-musl-arm64"];
            var availableRids = new List<string>();

            foreach (string rid in knownRids)
            {
                string extension = rid.StartsWith("win", StringComparison.Ordinal) ? ".exe" : string.Empty;
                bool linked = RepoContext.AvaloniaTemplateTfms.All(tfm =>
                    File.Exists(Path.Combine(artifactRoot, tfm, rid, $"apphost{extension}"))
                    && File.Exists(Path.Combine(artifactRoot, tfm, rid, $"singlefilehost{extension}")));

                if (linked)
                {
                    availableRids.Add(rid);
                }
            }

            if (availableRids.Count == 0)
            {
                throw new InvalidOperationException(
                    $"No Avalonia apphost templates are linked for all of {string.Join(", ", RepoContext.AvaloniaTemplateTfms)} under {artifactRoot}.");
            }

            // Pack each available RID
            foreach (string rid in availableRids)
            {
                string cacheDir = Path.Combine(
                    packagesCache,
                    $"chsbuffer.avalonia.apphost.{rid.ToLowerInvariant()}");

                if (Directory.Exists(cacheDir))
                {
                    Directory.Delete(cacheDir, recursive: true);
                }

                CommandResult result = await CommandRunner.RunAsync(
                    "dotnet",
                    $"pack \"{ridProject}\" -c Release -v:minimal -p:AvaloniaHostRid={rid}",
                    RepoContext.RepoRoot,
                    cancellationToken);

                if (result.ExitCode != 0)
                {
                    throw new InvalidOperationException(
                        $"Failed to pack Avalonia RID package for '{rid}'.{Environment.NewLine}{result.CombinedOutput}");
                }
            }

            // Pack Build package
            {
                string cacheDir = Path.Combine(packagesCache, "chsbuffer.avalonia.apphost.build");
                if (Directory.Exists(cacheDir))
                {
                    Directory.Delete(cacheDir, recursive: true);
                }

                CommandResult result = await CommandRunner.RunAsync(
                    "dotnet",
                    $"pack \"{buildProject}\" -c Release -v:minimal{BuildSysrootPropertyArgument()}",
                    RepoContext.RepoRoot,
                    cancellationToken);

                if (result.ExitCode != 0)
                {
                    throw new InvalidOperationException(
                        $"Failed to pack Avalonia Build package.{Environment.NewLine}{result.CombinedOutput}");
                }
            }

            _packed = true;
        }
        finally
        {
            Lock.Release();
        }
    }

    private static string BuildSysrootPropertyArgument()
    {
        string? rootfsDir = Environment.GetEnvironmentVariable(RootfsDirEnvironmentVariableName);
        if (string.IsNullOrWhiteSpace(rootfsDir))
        {
            return string.Empty;
        }

        return $" -p:Sysroot=\"{rootfsDir.Trim()}\"";
    }
}

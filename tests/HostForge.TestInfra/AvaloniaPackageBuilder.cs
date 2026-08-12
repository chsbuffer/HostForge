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

            // Discover available RIDs from linked artifacts
            string artifactRoot = Path.Combine(
                RepoContext.RepoRoot,
                "artifacts", "avalonia-host", RepoContext.TargetAvaloniaVersion);
            string[] knownRids = ["win-x64", "win-arm64", "linux-x64"];
            var availableRids = new List<string>();

            foreach (string rid in knownRids)
            {
                string ridDir = Path.Combine(artifactRoot, rid);
                string apphostName = rid.StartsWith("win", StringComparison.Ordinal) ? "apphost.exe" : "apphost";
                string singlefilehostName = rid.StartsWith("win", StringComparison.Ordinal) ? "singlefilehost.exe" : "singlefilehost";

                if (File.Exists(Path.Combine(ridDir, apphostName))
                    && File.Exists(Path.Combine(ridDir, singlefilehostName)))
                {
                    availableRids.Add(rid);
                }
            }

            if (availableRids.Count == 0)
            {
                throw new InvalidOperationException(
                    $"No linked Avalonia apphost templates found under {artifactRoot}.");
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

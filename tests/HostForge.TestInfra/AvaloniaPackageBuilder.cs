namespace HostForge.TestInfra;

public static class AvaloniaPackageBuilder
{
    private static readonly SemaphoreSlim Lock = new(1, 1);
    private static bool _packed;

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

            // Consumers restore into RepoContext.TestPackagesRoot, which holds none of these packages
            // at the start of a test run, so they get the packages packed here.
            foreach (string rid in availableRids)
            {
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

            CommandResult buildResult = await CommandRunner.RunAsync(
                "dotnet",
                $"pack \"{buildProject}\" -c Release -v:minimal",
                RepoContext.RepoRoot,
                cancellationToken);

            if (buildResult.ExitCode != 0)
            {
                throw new InvalidOperationException(
                    $"Failed to pack Avalonia Build package.{Environment.NewLine}{buildResult.CombinedOutput}");
            }

            _packed = true;
        }
        finally
        {
            Lock.Release();
        }
    }
}

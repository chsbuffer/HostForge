namespace HostForge.TestInfra;

public static class AvaloniaPackageBuilder
{
    private static readonly SemaphoreSlim Lock = new(1, 1);
    private static readonly HashSet<string> PackedModes = new(StringComparer.Ordinal);

    public static async Task EnsurePackedAsync(
        string packageMode = "windows",
        CancellationToken cancellationToken = default)
    {
        if (PackedModes.Contains(packageMode))
        {
            return;
        }

        await Lock.WaitAsync(cancellationToken);
        try
        {
            if (PackedModes.Contains(packageMode))
            {
                return;
            }

            string project = Path.Combine(
                RepoContext.RepoRoot,
                "src",
                "package-avalonia-apphost",
                "AvaloniaAppHost.csproj");
            string packageCacheDir = Path.Combine(
                RepoContext.RepoRoot,
                "artifacts",
                "tmp",
                "nuget",
                "packages",
                RepoContext.GetAvaloniaPackageId(packageMode).ToLowerInvariant());

            if (Directory.Exists(packageCacheDir))
            {
                Directory.Delete(packageCacheDir, recursive: true);
            }

            CommandResult result = await CommandRunner.RunAsync(
                "dotnet",
                RepoContext.AppendTargetAvaloniaVersionProperty($"pack \"{project}\" -c Release -v:minimal -p:AvaloniaAppHostPackageMode={packageMode}"),
                RepoContext.RepoRoot,
                cancellationToken);

            if (result.ExitCode != 0)
            {
                throw new InvalidOperationException(
                    $"Failed to pack Avalonia package for mode '{packageMode}'.{Environment.NewLine}{result.CombinedOutput}");
            }

            PackedModes.Add(packageMode);
        }
        finally
        {
            Lock.Release();
        }
    }
}

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
                "chsbuffer.avalonia.apphost");

            if (Directory.Exists(packageCacheDir))
            {
                Directory.Delete(packageCacheDir, recursive: true);
            }

            CommandResult result = await CommandRunner.RunAsync(
                "dotnet",
                RepoContext.AppendTargetAvaloniaVersionProperty($"pack \"{project}\" -c Release -v:minimal"),
                RepoContext.RepoRoot,
                cancellationToken);

            if (result.ExitCode != 0)
            {
                throw new InvalidOperationException(
                    $"Failed to pack Avalonia package.{Environment.NewLine}{result.CombinedOutput}");
            }

            _packed = true;
        }
        finally
        {
            Lock.Release();
        }
    }
}

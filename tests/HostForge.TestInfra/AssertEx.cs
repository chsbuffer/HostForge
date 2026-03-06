namespace HostForge.TestInfra;

public static class AssertEx
{
    public static void Success(CommandResult result, string? step = null)
    {
        if (result.ExitCode != 0)
        {
            string prefix = string.IsNullOrEmpty(step)
                ? "Command failed"
                : $"Step {step} failed";

            throw new InvalidOperationException(
                $"{prefix} with exit code {result.ExitCode}.{Environment.NewLine}{result.CombinedOutput}");
        }
    }

    public static void Contains(string value, string expected)
    {
        if (!value.Contains(expected, StringComparison.Ordinal))
        {
            throw new InvalidOperationException(
                $"Expected text not found: {expected}{Environment.NewLine}{value}");
        }
    }

    public static void NotContains(string value, string unexpected)
    {
        if (value.Contains(unexpected, StringComparison.Ordinal))
        {
            throw new InvalidOperationException(
                $"Unexpected text found: {unexpected}{Environment.NewLine}{value}");
        }
    }

    public static void FileExists(string path)
    {
        if (!File.Exists(path))
        {
            throw new InvalidOperationException($"Expected file was not found: {path}");
        }
    }

    public static void FileMissing(string path)
    {
        if (File.Exists(path))
        {
            throw new InvalidOperationException($"File should not exist: {path}");
        }
    }
}

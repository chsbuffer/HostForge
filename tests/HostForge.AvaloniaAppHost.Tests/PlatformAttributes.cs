using TUnit.Core;

namespace HostForge.AvaloniaAppHost.Tests;

public sealed class WindowsOnlyAttribute : SkipAttribute
{
    public WindowsOnlyAttribute()
        : base("Windows only")
    {
    }

    public override Task<bool> ShouldSkip(TestRegisteredContext testContext)
        => Task.FromResult(!OperatingSystem.IsWindows());
}

public sealed class LinuxOnlyAttribute : SkipAttribute
{
    public LinuxOnlyAttribute()
        : base("Linux only")
    {
    }

    public override Task<bool> ShouldSkip(TestRegisteredContext testContext)
        => Task.FromResult(!OperatingSystem.IsLinux());
}

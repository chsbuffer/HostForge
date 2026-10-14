# HostForge.AvaloniaAppHost.Tests

TUnit-based integration tests for HostForge MSBuild behavior.

Current coverage:

- Windows-only package contract validation for `ChsBuffer.Avalonia.AppHost.Windows`
- Windows activation and inactive-warning behavior across `net9.0` / `net10.0`, `win-x64` / `win-arm64`
- Windows Skia/HarfBuzz native runtime copy suppression switch behavior
- Windows ANGLE P/Invoke resolution from the apphost and runtime copy suppression
- Linux `linux-x64` publish-and-run output validation for `ChsBuffer.Avalonia.AppHost.Linux`
- Linux `linux-x64` Skia/HarfBuzz native runtime copy suppression switch behavior
- Skia 3 / 4 package versions, optional preview labels, managed dependencies and unchanged template paths

Run:

```powershell
dotnet test --project .\tests\HostForge.AvaloniaAppHost.Tests\HostForge.AvaloniaAppHost.Tests.csproj -c Release -v:minimal
```

Platform notes:

- Windows-only tests are skipped on non-Windows runners.
- Linux-only tests are skipped on non-Linux runners.

After linking the Skia 4 templates, run `task avalonia-test SKIASHARP_VERSION=4.153.1`.
This passes the selected Skia version to package generation and consumer projects,
and publish tests check that the native version matches the selected managed line.

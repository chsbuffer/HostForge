# HostForge.AvaloniaAppHost.Tests

TUnit-based integration tests for HostForge MSBuild behavior.

Current coverage:

- Windows-only package contract validation for `ChsBuffer.Avalonia.AppHost.Windows`: templates for every
  `AvaloniaTemplateTfms` entry and generated `AvaloniaAppHostRids` registrations evaluated by MSBuild
- Windows activation and inactive-warning behavior across `net9.0` (inactive), `net10.0`, `net11.0` and
  `net11.0-windows`, `win-x64` / `win-arm64`
- Windows Skia/HarfBuzz native runtime copy suppression switch behavior
- Windows ANGLE P/Invoke resolution from the apphost and runtime copy suppression for every template TFM
- Self-contained single-file publish on Windows and Linux for every template TFM, without and with
  `IncludeNativeLibrariesForSelfExtract`: `RuntimeFrameworkVersion` is pinned to the template's runtime version,
  as the package README requires; the bundle runs on the prelinked singlefilehost (exact runtime version checked)
  and resolves ANGLE / Skia / HarfBuzz from the executable, with none of their dynamic libraries (or, on Windows, their
  symbol files) in the publish directory or extracted from the bundle (`DOTNET_BUNDLE_EXTRACT_BASE_DIR` points extraction into the test project)
- Linux publish-and-run output validation for every template TFM, on the RID of the test system (`linux-x64` on
  glibc, `linux-musl-x64` / `linux-musl-arm64` on Alpine; `HOSTFORGE_NATIVE_RID` overrides it)
- Publish without a RuntimeIdentifier on Windows and Linux for every template TFM: no inactive warning, the native
  libraries of every RID stay under `runtimes/`, and the portable output runs on `dotnet`
- Windows `UseAppHost=false` and NativeAOT publishes: inactive with the matching warning, the dynamic libraries stay in
  the publish directory, and the output runs on `dotnet` or as the NativeAOT executable
- Windows F# project: builds and publishes inactive with the language warning, keeping the dynamic libraries
- Linux ELF audit of the linked templates: architecture, the glibc or musl program interpreter, no glibc
  dependency in the musl templates, and the diagnostics exports of the upstream singlefilehost
- Linux Skia/HarfBuzz native runtime copy suppression switch behavior
- Skia 3 / 4 package versions, optional preview labels, managed dependencies and unchanged template paths

Link the templates for every template TFM first (`task link-avalonia`). Running the published executables
needs the matching shared runtimes: .NET 9, 10 and 11 on Windows; .NET 10 and 11 on Linux. The Windows NativeAOT
publish needs the MSVC build tools. The Linux ELF audit needs `readelf` (binutils).

Run:

```powershell
dotnet test --project .\tests\HostForge.AvaloniaAppHost.Tests\HostForge.AvaloniaAppHost.Tests.csproj -c Release -v:minimal
```

Platform notes:

- Windows-only tests are skipped on non-Windows runners.
- Linux-only tests are skipped on non-Linux runners.

After linking the Skia 4 templates, run `task avalonia-test SKIASHARP_VERSION=<version>` with the
SkiaSharp 4 version from `native/skiasharp/versions.json`.
This passes the selected Skia version to package generation and consumer projects,
and publish tests check that the native version matches the selected managed line.

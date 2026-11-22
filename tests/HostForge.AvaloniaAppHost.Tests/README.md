# HostForge.AvaloniaAppHost.Tests

TUnit-based integration tests for HostForge MSBuild behavior.

Current coverage:

- Windows package contract validation: the RID packages `ChsBuffer.Avalonia.AppHost.win-x64` / `win-arm64` contain
  templates for every `AvaloniaTemplateTfms` entry and generated `AvaloniaAppHostRids` registrations evaluated by
  MSBuild
- Windows RID package dependencies: the Build package and the SkiaSharp, HarfBuzzSharp and ANGLE versions that
  the templates link
- Consumer projects reference only a RID package, as applications do. Windows activation across `net10.0`,
  `net11.0` and `net11.0-windows`, `win-x64` / `win-arm64`; `net9.0`, for which the RID packages have no
  dependencies, keeps the SDK host and the native libraries without a warning; publishing for a RID whose package
  the project does not reference keeps them with the inactive warning
- RID package license file on Windows and Linux: `THIRD-PARTY-NOTICES.txt` has sections for the .NET runtime
  of every template TFM, Skia and HarfBuzz, ANGLE on Windows only, and libc++ for Linux SkiaSharp 4, and
  reproduces each file in the `licenses/` directories of the packed templates as a section headed by its path
- Windows PE audit of the linked templates: Control Flow Guard for every RID and, as in the official hosts, the
  EH continuation table (`/guard:ehcont`) for `win-x64`
- Windows Skia/HarfBuzz native runtime copy suppression switch behavior
- Windows ANGLE P/Invoke resolution from the apphost and runtime copy suppression for every template TFM: the
  consumer references Avalonia.Win32 and calls its `EGL_GetProcAddress` import, which the module initializer
  resolves from the executable
- The other consumer projects do not reference Avalonia.Win32, so their runs on a Windows template also check that
  the module initializer registers its resolver only with referenced assemblies
- The consumer programs call SkiaSharp, SkiaSharp.Skottie, SkiaSharp.SceneGraph and SkiaSharp.Resources, which
  each import libSkiaSharp, and HarfBuzzSharp; every run on a template checks that no Skia, HarfBuzz or ANGLE
  library file is loaded
- Self-contained single-file publish on Windows and Linux for every template TFM, without and with
  `IncludeNativeLibrariesForSelfExtract`: `RuntimeFrameworkVersion` is pinned to the template's runtime version,
  as the package README requires; the bundle runs on the prelinked singlefilehost (exact runtime version checked)
  and resolves ANGLE / Skia / HarfBuzz from the executable, with none of their dynamic libraries (or, on Windows, their
  symbol files) in the publish directory or extracted from the bundle (`DOTNET_BUNDLE_EXTRACT_BASE_DIR` points extraction into the test project)
- Linux publish-and-run output validation for every template TFM, on the RID of the test system (`linux-x64` on
  glibc, `linux-musl-x64` / `linux-musl-arm64` on Alpine; `HOSTFORGE_NATIVE_RID` overrides it)
- Build output on Windows and Linux for every template TFM: runs on the template, and on `dotnet`, whose main
  program exports no native library, the resolver of the application assembly leaves them to the build output
- Publish without a RuntimeIdentifier on Windows and Linux for every template TFM: no inactive warning, the native
  libraries of every RID stay under `runtimes/`, and the portable output runs on `dotnet`
- Windows `UseAppHost=false` and NativeAOT publishes: inactive with the matching warning, the dynamic libraries stay in
  the publish directory, and the output runs on `dotnet` or as the NativeAOT executable
- Windows F# project: builds and publishes inactive with the language warning, keeping the dynamic libraries
- Linux: the linked singlefilehost exports the diagnostics symbols of the upstream singlefilehost
- Linux Skia/HarfBuzz native runtime copy suppression switch behavior
- Dependencies of the Linux RID package of the test system's RID on `SkiaSharp.NativeAssets.Linux` and
  `HarfBuzzSharp.NativeAssets.Linux` at the managed versions

Link the templates for every template TFM first: `task link-avalonia TARGET_OS=windows` on Windows, the container
commands in [Linux templates](../../src/package-avalonia-apphost/DEVELOPMENT.md#linux-templates) on Linux.
Running the published executables needs the matching shared runtimes: .NET 9, 10 and 11 on Windows; .NET 10 and 11
on Linux. The Windows NativeAOT publish needs the MSVC build tools. The Linux export check needs `readelf` (binutils).

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

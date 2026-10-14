# Avalonia AppHost Packaging Notes

This file is for repository maintainers. The NuGet README is intentionally limited to package usage.

## Artifact layout

The linker consumes native inputs from:

- `artifacts/hostlibs/<runtime-version>/<flavor>/<rid>/`
- `artifacts/skiasharp/<skiasharp-version>/<rid>/`
- `artifacts/angle/<angle-version>/<rid>/` for Windows

Linked host templates are generated under:

- `artifacts/avalonia-host/<version>/<rid>/`

Examples:

- `artifacts/avalonia-host/12.0/win-x64/apphost.exe`
- `artifacts/avalonia-host/12.0/linux-x64/singlefilehost`

## Projects

| Project | Purpose |
|---|---|
| `AvaloniaAppHost.Link.proj` | Links apphost templates for one OS at a time (`TARGET_OS=windows\|linux`) |
| `AvaloniaAppHost.Rids.targets` | Shared RID definitions for link/pack orchestration |
| `AvaloniaAppHost.Rid.csproj` | Packs one RID's templates + `.props` (`-p:AvaloniaHostRid=win-x64`) |
| `AvaloniaAppHost.Build.csproj` | Packs `.targets` + `ModuleInitializer.cs` |
| `AvaloniaAppHost.csproj` | Meta-package: depends on all RID + Build packages |

## Package layout

Per-RID packages (`ChsBuffer.Avalonia.AppHost.win-x64`, etc.) register their RID via `.props`:

```xml
<AvaloniaAppHostRids Include="win-x64">
  <AppHostTemplateTfm>net10.0</AppHostTemplateTfm>
  <AppHostTemplatePath>…/template/net10.0/win-x64/apphost.exe</AppHostTemplatePath>
  <SingleFileHostTemplatePath>…/template/net10.0/win-x64/singlefilehost.exe</SingleFileHostTemplatePath>
</AvaloniaAppHostRids>
```

The Build package's `.targets` matches `$(RuntimeIdentifier)` against `@(AvaloniaAppHostRids)`. Each RID package depends on the Build package, so consumers install only the RID packages they need. The meta-package `ChsBuffer.Avalonia.AppHost` installs every supported RID package.

## SkiaSharp variants and package versions

`SKIASHARP_VERSION` selects the native libraries and managed package dependencies
for link, pack and test commands. The default is `3.119.4`; use `4.153.1` for Skia 4.
Package IDs are shared, and the Skia 4 variant adds `skia4` to the prerelease part
of every RID, Build and meta-package version:

| `_Preview` | SkiaSharp 3.119.4 | SkiaSharp 4.153.1 |
|---|---|---|
| empty | `12.0.0` | `12.0.0-skia4` |
| `beta` | `12.0.0-beta` | `12.0.0-beta.skia4` |

Set `_Preview` in `Directory.Build.props`, pass `-p:_Preview=beta` directly to
MSBuild, or set the `_Preview` environment variable before running Task commands.
The `skia4` suffix is added automatically; keep `_Preview` as the desired base label.

Linked templates keep the same `artifacts/avalonia-host/<version>/<rid>/` paths.
For local builds, link and pack one Skia variant before linking the other variant.
CI uses separate jobs and `avalonia-host-skia4-windows` / `avalonia-host-skia4-linux`
artifacts, then uploads the five Skia 4 packages as `nuget-avalonia-apphost-skia4`.

## Local commands

Link one OS:

```powershell
task link-avalonia TARGET_OS=windows
task link-avalonia TARGET_OS=linux SYSROOT=build/rootfs/x64
```

Select Skia 4 with the same option for every step:

```powershell
task link-avalonia TARGET_OS=windows SKIASHARP_VERSION=4.153.1
task avalonia-test SKIASHARP_VERSION=4.153.1
task pack-avalonia SKIASHARP_VERSION=4.153.1
```

Before packing all RIDs, link Linux in its native environment with
`task link-avalonia TARGET_OS=linux SKIASHARP_VERSION=4.153.1 SYSROOT=build/rootfs/x64`.

Pack one RID (requires linked templates for that RID):

```powershell
task pack-avalonia-rid RID=win-x64
task pack-avalonia-rid RID=linux-x64
```

Pack Build package:

```powershell
task pack-avalonia-build
```

Pack everything (RIDs + Build + meta):

```powershell
task pack-avalonia
```

Pack only selected RIDs directly:

```powershell
dotnet pack src/package-avalonia-apphost/AvaloniaAppHost.Rid.csproj -c Release -p:AvaloniaHostRid=win-x64
```

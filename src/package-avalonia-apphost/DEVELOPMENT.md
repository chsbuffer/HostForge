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

- `AvaloniaAppHost.Link.proj`
  - links apphost templates for one OS at a time
- `AvaloniaAppHost.csproj`
  - packs the NuGet package from linked artifacts
- `AvaloniaAppHost.Rids.targets`
  - shared RID definitions for link/pack orchestration

## Pack modes

`AvaloniaAppHost.csproj` requires `AvaloniaAppHostPackageMode`:

- `windows`
  - package id: `ChsBuffer.Avalonia.AppHost.Windows`
  - auto-runs `AvaloniaAppHost.Link.proj` for Windows RIDs
- `linux`
  - package id: `ChsBuffer.Avalonia.AppHost.Linux`
  - auto-runs `AvaloniaAppHost.Link.proj` for Linux RIDs
- `all`
  - package id: `ChsBuffer.Avalonia.AppHost`
  - does not link anything
  - expects pre-generated artifacts for every required RID
  - intended for CI aggregate packing

## Local commands

Link one OS:

```powershell
python .\scripts\pipeline.py link-avalonia -v --os windows
python .\scripts\pipeline.py link-avalonia -v --os linux --sysroot repo/rootfs/x64
```

Pack one OS:

```powershell
python .\scripts\pipeline.py pack-avalonia -v --mode windows
python .\scripts\pipeline.py pack-avalonia -v --mode linux
```

Pack only selected RIDs by passing the optional semicolon-separated `AvaloniaHostRids` property:

```powershell
dotnet pack .\src\package-avalonia-apphost\AvaloniaAppHost.csproj -c Release `
  -p:AvaloniaAppHostPackageMode=windows `
  -p:AvaloniaHostRids=win-x64
```

Pack aggregate CI-style package:

```powershell
python .\scripts\pipeline.py pack-avalonia -v --mode all
```

`all` should only be used after downloading or generating both Windows and Linux artifacts.

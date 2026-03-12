# Avalonia AppHost Packaging Notes

This file is for repository maintainers. The NuGet README is intentionally limited to package usage.

## Artifact layout

Linked host templates are generated under:

- `artifacts/avalonia-host/<TargetAvaloniaVersion>/<rid>/`

Examples:

- `artifacts/avalonia-host/11.0/win-x64/apphost.exe`
- `artifacts/avalonia-host/11.0/linux-x64/singlefilehost`

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
python .\scripts\pipeline.py link-avalonia -v --target 11.0 --os windows
python .\scripts\pipeline.py link-avalonia -v --target 11.0 --os linux --sysroot repo/rootfs/x64
```

Pack one OS:

```powershell
python .\scripts\pipeline.py pack-avalonia -v --target 11.0 --mode windows
python .\scripts\pipeline.py pack-avalonia -v --target 11.0 --mode linux
```

Pack aggregate CI-style package:

```powershell
python .\scripts\pipeline.py pack-avalonia -v --target 11.0 --mode all
```

`all` should only be used after downloading or generating both Windows and Linux artifacts.

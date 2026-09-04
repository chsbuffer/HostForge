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

## Local commands

Link one OS:

```powershell
task link-avalonia TARGET_OS=windows
task link-avalonia TARGET_OS=linux SYSROOT=build/rootfs/x64
```

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

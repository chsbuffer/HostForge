# avalonia-sample

Minimal `win-x64` Avalonia desktop sample that consumes `ChsBuffer.Avalonia.AppHost.win-x64`.

## Prerequisites

1. Build the Windows x64 HostLibs of every runtime, SkiaSharp and ANGLE, as described in
   [DEVELOPMENT.md](../../DEVELOPMENT.md#windows-构建).
2. Link the `win-x64` templates, then pack the RID package and the Build package it depends on:

```powershell
task link-avalonia TARGET_OS=windows RID=win-x64
task pack-avalonia-rid RID=win-x64
task pack-avalonia-build
```

The packages are written to `artifacts/packages/Release`, the only source from which the repository's
`NuGet.Config` restores `ChsBuffer.*` packages.

NuGet does not extract a package version again once it is in the package folder (`artifacts/tmp/nuget/packages`,
or `NUGET_PACKAGES` when set). After repacking under the same version, delete the `chsbuffer.avalonia.apphost*`
folders there before building the sample again.

## Build and run

```powershell
dotnet build .\samples\avalonia-sample\AvaloniaSample.csproj -c Release
dotnet run --project .\samples\avalonia-sample\AvaloniaSample.csproj -c Release
```

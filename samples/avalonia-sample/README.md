# avalonia-sample

Minimal Avalonia desktop sample that consumes `ChsBuffer.Avalonia.AppHost`.

## Prerequisites

1. Build/package the local Avalonia apphost package:

```powershell
task link-avalonia TARGET_OS=windows
task pack-avalonia
```

2. Ensure the produced nupkg exists under:

`artifacts/packages/Release`

NuGet does not extract a package version again once it is in the package folder (`artifacts/tmp/nuget/packages`,
or `NUGET_PACKAGES` when set). After repacking under the same version, delete the `chsbuffer.avalonia.apphost*`
folders there before building the sample again.

## Build and run

```powershell
dotnet build .\samples\avalonia-sample\AvaloniaSample.csproj -c Release
dotnet run --project .\samples\avalonia-sample\AvaloniaSample.csproj -c Release
```

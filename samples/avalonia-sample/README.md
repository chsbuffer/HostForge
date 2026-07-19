# avalonia-sample

Minimal Avalonia desktop sample that consumes `ChsBuffer.Avalonia.AppHost`.

## Prerequisites

1. Build/package the local Avalonia apphost package:

```powershell
python .\scripts\pipeline.py link-avalonia -v --os windows
python .\scripts\pipeline.py pack-avalonia -v --mode windows
```

2. Ensure the produced nupkg exists under:

`artifacts/packages/Release`

## Build and run

```powershell
dotnet build .\samples\avalonia-sample\AvaloniaSample.csproj -c Release
dotnet run --project .\samples\avalonia-sample\AvaloniaSample.csproj -c Release
```

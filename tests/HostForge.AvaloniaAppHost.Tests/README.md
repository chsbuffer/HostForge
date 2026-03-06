# HostForge.AvaloniaAppHost.Tests

TUnit-based integration tests for HostForge MSBuild behavior.

Current coverage:

- package template RID coverage (`win-x64` required, `win-arm64` conditional on local artifacts)
- `ChsBuffer.Avalonia.AppHost` inactive warning on non-supported TFM/RID
- activation on `net10.0/win-x64`
- Skia/HarfBuzz native DLL publish suppression switch behavior

Run:

```powershell
dotnet test --project .\tests\HostForge.AvaloniaAppHost.Tests\HostForge.AvaloniaAppHost.Tests.csproj -c Release -v:minimal
```

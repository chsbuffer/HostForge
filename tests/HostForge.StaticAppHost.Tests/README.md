# HostForge.StaticAppHost.Tests

TUnit matrix tests for `ChsBuffer.NETCore.StaticAppHost.win-x64`.

Run:

```powershell
dotnet test --project .\tests\HostForge.StaticAppHost.Tests\HostForge.StaticAppHost.Tests.csproj -c Release -v:minimal
```

Optional env flags:

- `HOSTFORGE_MATRIX_SKIP_EXE_RUN=true`
- `HOSTFORGE_MATRIX_NO_CLEAN=true`

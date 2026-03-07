# HostForge.StaticAppHost.Tests

TUnit matrix tests for the source-based `StaticAppHost.targets` integration.

Run:

```powershell
dotnet test --project .\tests\HostForge.StaticAppHost.Tests\HostForge.StaticAppHost.Tests.csproj -c Release -v:minimal
```

Optional env flags:

- `HOSTFORGE_MATRIX_SKIP_EXE_RUN=true`
- `HOSTFORGE_MATRIX_NO_CLEAN=true`

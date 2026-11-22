# HostForge.StaticAppHost.Tests

TUnit matrix tests for the source-based `StaticAppHost.targets` integration. The matrix runs once per
`HostForgeTargetFrameworks` entry in `Directory.Build.props`, relinking `samples/simple-pinvoke` against the
host libs of that runtime, so build HostLibs for every runtime version first. On Linux the .NET 11 host libs
contain LLVM bitcode: run the matrix with a Clang and LLD at least as new as the LLVM of the runtime's build
container, as CI does in the `build` stage of `native/hostlibs/linux.Dockerfile`. On Windows the matrix also
checks that the relinked hosts keep Control Flow Guard and the EH continuation table of the official x64 hosts,
and that `StaticHostGuardEHCont=false` relinks without the table.

Run:

```powershell
dotnet test --project .\tests\HostForge.StaticAppHost.Tests\HostForge.StaticAppHost.Tests.csproj -c Release -v:minimal
```

Optional env flags:

- `HOSTFORGE_MATRIX_SKIP_EXE_RUN=true`: build and publish without running the executable.
- `HOSTFORGE_MATRIX_NO_CLEAN=true`: keep the `bin` / `obj` of `samples/simple-pinvoke` from the previous run, to
  check incremental relinking after changing an input. The first build of each kind then relinks only if its
  inputs changed, so only the repeated builds are checked: they must not relink.

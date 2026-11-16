# HostLibs Conan recipe

This Conan 2 recipe builds and packages the .NET Runtime apphost and single-file
host static link inputs. `pgo=True` is the default package; set `pgo=False` for
the Windows no-PGO flavor.

Sources come from `src/runtime` of the .NET VMR (`dotnet/dotnet`) at the 1xx SDK
band tag that built each shipped runtime, as listed in [`versions.json`](versions.json).
The VMR, not `dotnet/runtime`, is the source of the official builds and receives
fixes (including security fixes) first. The recipe sparse-checks out only
`src/runtime` and verifies that the tag resolves to the recorded commit, which is
also the first line of `shared/Microsoft.NETCore.App/<version>/.version` in an
installed runtime.

Select the runtime with the Conan version (`--version`, required). Through Task,
`HOSTLIBS_VERSION` defaults to the runtime of `HostLibsTargetFramework` in
`Directory.Build.props`:

```sh
task build-hostlibs
task build-hostlibs HOSTLIBS_VERSION=<runtime version from versions.json>
```

The upstream workflow opens a pull request that updates `versions.json` to the latest
runtime patch releases; `task upstream-apply -- --dotnet` does the same locally.

.NET 11 rewrote apphost in C, so the recipe accepts both the C and the C++ Ninja
link rules when it collects link inputs, and ends the explicit inputs at any Ninja
dependency separator (`|`, `||`, `|@`). Libraries passed inside
`/WHOLEARCHIVE:<lib>` (.NET 11 links `runtimeinfo.lib` that way) are packaged too.

Each payload records the runtime's own compile and link flags in
`<host>.linkflags`; the Windows relink reads the `/DELAYLOAD` options from
`singlefilehost.linkflags`, because .NET 10 and .NET 11 delay-load different DLLs.

Set `CONAN_HOME` to an absolute path (e.g. `$PWD/build/conan`). The
Runtime build has deeply nested paths and long MSVC command lines.

The deployer preserves the response-file-relative directory tree expected by the
existing MSBuild targets and writes it below
`artifacts/hostlibs/<runtime-version>/<flavor>/<rid>`.

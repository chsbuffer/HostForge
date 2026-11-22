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

On Linux, Task's `RID` selects the host profile: `linux-x64` (the default),
`linux-musl-x64` or `linux-musl-arm64`. The musl profiles set `libc=musl`, and the
recipe passes `--os linux-musl` to the Runtime's `build.sh`, which selects the musl
RID, its CMake definitions and the PGO data as in the official builds. Every Linux RID
builds in the official build container of the runtime and RID, which `versions.json`
records under `containers`, against the rootfs of that container (`SYSROOT`, passed to
`build.sh --cross` as `ROOTFS_DIR`); the `build` stage of [`linux.Dockerfile`](linux.Dockerfile)
adds the tools that run Task, at the versions of their pins (`python scripts/upstream.py build-args`):

```sh
docker build $(python scripts/upstream.py build-args) -f native/hostlibs/linux.Dockerfile --target build --build-arg IMAGE=<container> -t hostforge-hostlibs .
docker run --rm -v "$PWD:/work" hostforge-hostlibs task build-hostlibs RID=linux-x64 HOSTLIBS_VERSION=<runtime version> SYSROOT=/crossrootfs/x64
```

The upstream LLVM of the container applies the profile data that the Runtime restores. The
.NET 11 PGO build compiles the GC with LTO, so `libcoreclr_static.a` contains LLVM bitcode,
and linking it needs an LLVM at least as new as the container's.

The upstream workflow takes the containers of a new runtime from
`src/runtime/eng/pipelines/common/templates/pipeline-with-resources.yml` at its VMR
commit and pins a container named by tag to the digest the tag has at that time.

.NET 11 rewrote apphost in C, so the recipe accepts both the C and the C++ Ninja
link rules when it collects link inputs, and ends the explicit inputs at any Ninja
dependency separator (`|`, `||`, `|@`). Libraries passed inside
`/WHOLEARCHIVE:<lib>` (.NET 11 links `runtimeinfo.lib` that way) are packaged too.

Each payload records the runtime's own compile and link flags in
`<host>.linkflags`; the Windows relink reads the `/DELAYLOAD` options from
`singlefilehost.linkflags`, because .NET 10 and .NET 11 delay-load different DLLs.

A link input outside the build tree, such as the `libpthread.a` stub that CMake finds
in the musl rootfs, is copied into the payload, and the response file names the copy.

Set `CONAN_HOME` to an absolute path (e.g. `$PWD/build/conan`). The
Runtime build has deeply nested paths and long MSVC command lines.

The deployer preserves the response-file-relative directory tree expected by the
existing MSBuild targets and writes it below
`artifacts/hostlibs/<runtime-version>/<flavor>/<rid>`.

# NativeAOT static graphics matrix

Tests package layout and dependencies (including license files that
`THIRD-PARTY-NOTICES.txt` reproduces), AOT/RID activation, version protection,
executable architecture, software pixels, HarfBuzz shaping and absence of dynamic
graphics libraries. The publish-and-run matrix covers every `HostForgeTargetFrameworks`
entry; `NativeAotMatrixTargetFramework` selects the TFM of
`samples/nativeaot-matrix`. A self-contained non-AOT publish of the same sample checks
that the dynamic libraries it loads report the versions of the managed packages. Build the native artifacts first; Windows also requires ANGLE.

```sh
task matrix-nativeaot
```

The task packs the SkiaSharp RID package, and on Windows the ANGLE one, for the RID
of the .NET SDK, then runs the tests with that RID and version as
`HOSTFORGE_NATIVE_RID` and `HOSTFORGE_SKIA_VERSION`. `SKIASHARP_VERSION` selects
another version from `native/skiasharp/versions.json`; the default is
`SkiaSharpVersion` in `Directory.Build.props`.

For packages already in `artifacts/packages/Release`, the only source of the
matrix's `ChsBuffer.*` packages, select the RID and version with `HOSTFORGE_NATIVE_RID`
and `HOSTFORGE_SKIA_VERSION`, then run:

```sh
dotnet test --project tests/HostForge.NativeAot.Tests -c Release
```

`HOSTFORGE_TEST_META=true` publishes through the three meta packages instead of the
RID packages and adds a build without a RID, with warnings treated as errors. The meta
packages restore the same RID packages, so CI tests RID consumers in the jobs that build
each RID package and meta consumers in `test-all`, both for every TFM. Without it, the
tests of the meta packages are skipped.

Run musl targets inside Alpine. `ROOTFS_DIR` selects the matching Linux sysroot
and enables ABI version checks. `HOSTFORGE_NATIVE_RUNNER` can name an executable
wrapper for cross-architecture execution; macOS x64 on Apple Silicon needs Rosetta.
Renderer smoke commands are in the [sample README](../../samples/avalonia-nativeaot-smoke/README.md).

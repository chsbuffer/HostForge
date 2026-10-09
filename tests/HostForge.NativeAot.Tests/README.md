# NativeAOT static graphics matrix

Tests package layout, AOT/RID activation, version protection, executable
architecture, software pixels, HarfBuzz shaping and absence of dynamic graphics
libraries. Build the native artifacts first; Windows also requires ANGLE.

```sh
task matrix-nativeaot SKIASHARP_VERSION=3.119.4
```

For prepacked artifacts, select the RID and version with `HOSTFORGE_NATIVE_RID`
and `HOSTFORGE_SKIA_VERSION`, then run:

```sh
dotnet test --project tests/HostForge.NativeAot.Tests -c Release
```

`HOSTFORGE_TEST_META=true` adds three-meta-package consumers and
a build without a RID, with warnings treated as errors.

Run musl targets inside Alpine. `ROOTFS_DIR` selects the matching Linux sysroot
and enables ABI version checks. `HOSTFORGE_NATIVE_RUNNER` can name an executable
wrapper for cross-architecture execution; macOS x64 on Apple Silicon needs Rosetta.
Renderer smoke commands are in the [sample README](../../samples/avalonia-nativeaot-smoke/README.md).

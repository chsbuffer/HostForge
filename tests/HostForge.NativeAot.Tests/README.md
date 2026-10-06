# NativeAOT static graphics matrix

Run `task matrix-nativeaot` on an x64 Windows or Linux host after building the corresponding SkiaSharp native artifacts. Windows also requires ANGLE native artifacts. The task packs local RID packages, publishes `samples/nativeaot-matrix` with NativeAOT, runs it, and verifies that the published directory has no SkiaSharp, HarfBuzzSharp, or ANGLE dynamic library.

The package versions come from `Directory.Build.props` through MSBuild. Linux coverage is defined but must be run on a Linux host; NativeAOT output is executed on the build host.

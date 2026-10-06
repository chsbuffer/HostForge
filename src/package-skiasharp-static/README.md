# ChsBuffer.SkiaSharp.Static

Provides `win-x64`, `win-arm64`, and `linux-x64` static link inputs for SkiaSharp and HarfBuzzSharp in separate RID packages.

Through a `buildTransitive` props file, this package:

- Adds the bundled `.lib` or `.a` files as `NativeLibrary` items
- Adds `DirectPInvoke` for `libSkiaSharp` and `libHarfBuzzSharp`
- Marks the SkiaSharp and HarfBuzzSharp libraries as `WholeArchive`
- Appends the platform libraries required for linking

The `buildTransitive` targets file removes SkiaSharp and HarfBuzzSharp dynamic libraries from the publish file list after `ComputeResolvedFilesToPublishList`.

This package does not relink the .NET host by itself. It is intended to be used with NativeAOT or a compatible static AppHost package, both of which can consume `NativeLibrary` items and link them into the final executable.

On Windows, build with `task build-skiasharp ARCH=x64` or `task build-skiasharp ARCH=arm64`, then pack with `task pack-skia-static RID=win-x64` or `task pack-skia-static RID=win-arm64`. On Linux, build with `task build-skiasharp SYSROOT=<path>` and pack with `task pack-skia-static RID=linux-x64`.

## Why create this package when `2ndLAB.SkiaSharp.Static` already exists?

This package exists because the `libHarfBuzzSharp` library in `2ndLAB.SkiaSharp.Static` was not built with whole program optimization disabled.

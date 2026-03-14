# ChsBuffer.SkiaSharp.Static.win-x64

Provides `win-x64` static link inputs for SkiaSharp and HarfBuzzSharp.

Through a `buildTransitive` props file, this package:

- Adds the bundled `.lib` files as `NativeLibrary` items
- Marks `SkiaSharp.lib` and `libHarfBuzzSharp.lib` as `WholeArchive`
- Appends the Windows system libraries required for host relinking

This package does not relink the .NET host by itself. It is intended to be used together with `ChsBuffer.AppHost.Static.win-x64` or with NativeAOT, both of which can consume `NativeLibrary` items and link them into the final executable.

## Why create this package when `2ndLAB.SkiaSharp.Static` already exists?

This package exists because the `libHarfBuzzSharp` library in `2ndLAB.SkiaSharp.Static` was not built with whole program optimization disabled.

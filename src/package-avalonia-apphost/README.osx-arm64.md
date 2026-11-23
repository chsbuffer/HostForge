# ChsBuffer.Avalonia.AppHost.osx-arm64

Prelinked Avalonia 12 apphost and single-file host templates for .NET 10 and .NET 11 `osx-arm64`, with SkiaSharp and HarfBuzzSharp native libraries statically linked.

The templates use the macOS deployment target of their .NET runtime. Desktop applications can also depend on the separate Avalonia.Native library.

Use `ChsBuffer.Avalonia.AppHost` to include every supported RID; its README describes when the templates apply and the MSBuild properties.

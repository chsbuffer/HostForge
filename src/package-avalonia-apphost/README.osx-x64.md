# ChsBuffer.Avalonia.AppHost.osx-x64

Prelinked Avalonia 12 apphost and single-file host templates for .NET 10 and .NET 11 `osx-x64`, with SkiaSharp, HarfBuzzSharp and AvaloniaNative statically linked.

The templates use the macOS deployment target of their .NET runtime. Desktop applications using the matching Avalonia.Native package resolve it from the executable.

Use `ChsBuffer.Avalonia.AppHost` to include every supported RID; its README describes when the templates apply and the MSBuild properties.

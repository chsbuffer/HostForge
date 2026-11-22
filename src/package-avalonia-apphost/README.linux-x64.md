# ChsBuffer.Avalonia.AppHost.linux-x64

Prelinked Avalonia 12 apphost and single-file host templates for .NET 10 and .NET 11 `linux-x64`, with SkiaSharp and HarfBuzzSharp native libraries statically linked.

The executables need glibc 2.27 or later, as the .NET runtime does, and load `fontconfig` at startup.

Use `ChsBuffer.Avalonia.AppHost` to include every supported RID; its README describes when the templates apply and the MSBuild properties.

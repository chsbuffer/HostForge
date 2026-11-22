# ChsBuffer.Avalonia.AppHost.linux-musl-arm64

Prelinked Avalonia 12 apphost and single-file host templates for .NET 10 and .NET 11 `linux-musl-arm64` (Alpine Linux), with SkiaSharp and HarfBuzzSharp native libraries statically linked.

The executables load `fontconfig` at startup; install it with `apk add fontconfig`.

Use `ChsBuffer.Avalonia.AppHost` to include every supported RID; its README describes when the templates apply and the MSBuild properties.

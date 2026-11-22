# ChsBuffer.Avalonia.AppHost.linux-musl-arm64

Prelinked Avalonia 12 apphost and single-file host templates for .NET 10 and .NET 11 `linux-musl-arm64` (Alpine Linux), with SkiaSharp and HarfBuzzSharp native libraries statically linked.

The host libraries are built in the container and Alpine rootfs of the official .NET `linux-musl-arm64` builds, so the templates have the musl baseline of the .NET runtime. The executables load `fontconfig` at startup; install it with `apk add fontconfig`.

The package depends on SkiaSharp, HarfBuzzSharp and their Linux native assets at the linked versions, so builds the templates do not cover load matching dynamic libraries.

This package brings in the shared Build integration automatically. Use `ChsBuffer.Avalonia.AppHost` instead to include every supported RID.

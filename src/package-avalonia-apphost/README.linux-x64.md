# ChsBuffer.Avalonia.AppHost.linux-x64

Prelinked Avalonia 12 apphost and single-file host templates for .NET 10 and .NET 11 `linux-x64`, with SkiaSharp and HarfBuzzSharp native libraries statically linked.

The host libraries are built in the container and Ubuntu 18.04 rootfs of the official .NET `linux-x64` builds, with the same profile-guided optimizations, so the templates have the glibc baseline of the .NET runtime (glibc 2.27). The executables load `fontconfig` at startup.

The package depends on SkiaSharp, HarfBuzzSharp and their Linux native assets at the linked versions, so builds the templates do not cover load matching dynamic libraries.

This package brings in the shared Build integration automatically. Use `ChsBuffer.Avalonia.AppHost` instead to include every supported RID.

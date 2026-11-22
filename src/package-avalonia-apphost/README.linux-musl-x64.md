# ChsBuffer.Avalonia.AppHost.linux-musl-x64

Prelinked Avalonia 12 apphost and single-file host templates for .NET 10 and .NET 11 `linux-musl-x64` (Alpine Linux), with SkiaSharp and HarfBuzzSharp native libraries statically linked.

The host libraries are built in the container and Alpine rootfs of the official .NET `linux-musl-x64` builds, so the templates have the musl baseline of the .NET runtime. The executables load `fontconfig` at startup; install it with `apk add fontconfig`.

In projects that target .NET 10 or later, the package brings in the shared Build integration automatically and depends on SkiaSharp, HarfBuzzSharp and their Linux native assets at the linked versions, so builds the templates do not cover load matching dynamic libraries. It declares no dependencies for earlier .NET versions, which keep the SDK host without a warning.

`THIRD-PARTY-NOTICES.txt` in the package reproduces the licenses of the code linked into the templates: the .NET runtime of each template with its third-party notices, and Skia, HarfBuzz and the third-party libraries in their archives, with libc++ for SkiaSharp 4. Executables published with the templates contain this code, and these licenses ask binary distributions to reproduce their notices.

Use `ChsBuffer.Avalonia.AppHost` instead to include every supported RID.

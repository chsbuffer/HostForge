# ChsBuffer.Avalonia.AppHost.linux-musl-arm64

Prelinked Avalonia 12 apphost and single-file host templates for .NET 10 (10.0.15) and .NET 11 (11.0.0) on Alpine Linux ARM64, with SkiaSharp and HarfBuzzSharp statically linked.

Reference this RID package directly. The executable uses the system Fontconfig library (`fontconfig` on Alpine).

## Build locally

Use the same [Alpine 3.24 development packages](README.linux-musl-x64.md#build-locally)
on ARM64 and place the archives from `ChsBuffer.SkiaSharp.Static.linux-musl-arm64`
version `3.119.4.1` in `artifacts/skiasharp/3.119.4/linux-musl-arm64/`.

```sh
for runtime in 10.0.15 11.0.0; do
    go-task build-hostlibs HOSTLIBS_VERSION="$runtime" MUSL=True ARCH=arm64 JOBS=4
done
go-task link-avalonia TARGET_OS=linux-musl RID=linux-musl-arm64
_Preview=alpine.1 go-task pack-avalonia-build
_Preview=alpine.1 go-task pack-avalonia-rid RID=linux-musl-arm64
```

To cross-build from x64, use Clang with an ARM64 Alpine sysroot containing the
ARM64 GCC toolchain, development headers and libraries above, and `musl-utils`
so the .NET build can detect musl from `/usr/bin/ldd`. Pass the same absolute `SYSROOT=/path/to/arm64-sysroot` to both
`build-hostlibs` and `link-avalonia`.

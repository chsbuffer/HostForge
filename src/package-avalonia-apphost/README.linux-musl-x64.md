# ChsBuffer.Avalonia.AppHost.linux-musl-x64

Prelinked Avalonia 12 apphost and single-file host templates for Alpine Linux x64, with SkiaSharp and HarfBuzzSharp statically linked.

Reference this RID package directly. The executable uses the system Fontconfig library (`fontconfig` on Alpine).

## Build locally

Run from the repository root in an x64 Alpine 3.24 environment with the .NET 11 SDK:

```sh
apk add bash git build-base linux-headers clang lld llvm cmake ninja python3 uv go-task \
    icu-dev openssl-dev krb5-dev libunwind-dev lttng-ust-dev fontconfig-dev
go-task build-hostlibs HOSTLIBS_VERSION=11.0.0 MUSL=True JOBS=4
```

Place the SkiaSharp and HarfBuzzSharp archives from
`ChsBuffer.SkiaSharp.Static.linux-musl-x64` version `3.119.4.1` in
`artifacts/skiasharp/3.119.4/linux-musl-x64/`, or build the same inputs with the
repository's SkiaSharp task. Link the .NET 11 templates and pack the RID package:

```sh
go-task link-avalonia TARGET_OS=linux-musl TEMPLATE_TFM=net11.0
_Preview=alpine.1 go-task pack-avalonia-build
_Preview=alpine.1 go-task pack-avalonia-rid RID=linux-musl-x64 TEMPLATE_TFM=net11.0
```

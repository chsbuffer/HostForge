# AvaloniaNative tests

Tests package layout, version checks, AOT/RID activation, linker inputs and
dynamic library filtering. Run on any supported development OS:

```sh
dotnet test --project tests/HostForge.AvaloniaNative.Tests -c Release
```

On macOS, first run `task build-avalonianative` and
`task pack-avalonianative RID=osx-arm64` (use `RID=osx-x64` for both on Intel). The native test publishes
and runs a window that renders system, Chinese and emoji text. Other hosts skip
this execution test. Results are saved under `artifacts/avalonianative-smoke/<rid>/`.

To include static Skia, publish `Smoke/AvaloniaNativeSmoke.csproj` with
`-p:SmokeStaticSkiaVersion=<version>` (a version in `native/skiasharp/versions.json`)
after packing the matching Skia RID.

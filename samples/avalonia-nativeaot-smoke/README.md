# Avalonia NativeAOT renderer smoke

Publishes Avalonia 12.1.3 with SkiaSharp 3.119.4 or 4.153.1, draws one frame and
checks the actual Skia backend and red/green/blue/white pixels. A JSON result is
written to stdout; diagnostics go to stderr. Exit codes are 0 for success,
1 for failure and 124 for timeout. The default uses official dynamic native assets.

```sh
dotnet publish samples/avalonia-nativeaot-smoke -c Release -r linux-x64 -p:SkiaSharpVersion=4.153.1 -o artifacts/smoke
HOSTFORGE_SMOKE_VULKAN_VERSION=1.3.0 VK_DRIVER_FILES=/usr/share/vulkan/icd.d/lvp_icd.x86_64.json xvfb-run -a artifacts/smoke/AvaloniaNativeAotSmoke --mode Vulkan
```

Use the installed lavapipe manifest path. Linux requires X11, Mesa, fontconfig
and a Vulkan loader/ICD. SkiaSharp 4 on Linux requires Vulkan API 1.3 for this
probe. EGL/GLX can use `LIBGL_ALWAYS_SOFTWARE=1`; Windows ANGLE can select WARP
with `HOSTFORGE_SMOKE_ANGLE_ADAPTER=Microsoft Basic Render Driver`. macOS needs
an active GUI session and an awake display.

| Platform | `--mode` values |
| --- | --- |
| Windows | `AngleEgl`, `Vulkan`, `Wgl`, `Software` |
| Linux X11 | `Vulkan`, `Egl`, `Glx`, `Software` |
| macOS | `Metal`, `OpenGl`, `Software` |

To use local static packages, set `SmokePackageSource` to the package directory
and enable `SmokeUseStaticSkia`, `SmokeUseStaticAngle` or
`SmokeUseStaticAvaloniaNative` as MSBuild properties. Each defaults to the
corresponding `ChsBuffer.*.Static.<RID>` package. `--timeout-seconds` defaults to 30.

The acceptance test publishes both Skia versions and verifies each selected mode:

```sh
HOSTFORGE_SMOKE_REQUIRED=1 HOSTFORGE_SMOKE_MODES=Vulkan,Egl,Glx HOSTFORGE_SMOKE_VULKAN_VERSION=1.3.0 xvfb-run -a dotnet test --project tests/HostForge.NativeAot.Tests --treenode-filter '/*/*/AvaloniaSmokeTests/*'
```

Select a version with `HOSTFORGE_SMOKE_SKIA_VERSION`. The static MSBuild properties
can also be environment variables for this test; matching dynamic libraries must
then be absent from the published directory. Results are saved under
`artifacts/avalonia-smoke/<version>/<rid>/`.

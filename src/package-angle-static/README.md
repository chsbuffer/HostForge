# ChsBuffer.Angle.Static

Provides separate `win-x64` and `win-arm64` packages with ANGLE `libANGLE_static.lib` and `libGLESv2_static.lib` inputs, plus `av_libglesv2.def` for consumers that export ANGLE entry points from an apphost.

The `buildTransitive` props adds both libraries as `NativeLibrary` items, adds `DirectPInvoke` for `av_libglesv2`, and adds the required Windows system libraries. Apphost relinking consumers can use the `.def` file to export ANGLE entry points.

The `buildTransitive` targets file removes `av_libglesv2.dll` from the publish file list after `ComputeResolvedFilesToPublishList`.

Build the native libraries with `task build-angle ARCH=x64` or `task build-angle ARCH=arm64`, then pack with `task pack-angle-static RID=win-x64` or `task pack-angle-static RID=win-arm64`.

# ANGLE Conan recipe

This Conan 2 recipe owns ANGLE source checkout, patches, GN/Ninja
configuration, and the two complete static libraries. The custom deployer restores
the package to the artifact layout consumed by the existing MSBuild projects.

Add Chromium `depot_tools` to `PATH`, then build from the repository root:

```powershell
task build-angle ARCH=x64
task build-angle ARCH=arm64
```

The Conan package contains `libANGLE_static.lib`, `libGLESv2_static.lib`, and
`av_libglesv2.def`. MSBuild remains responsible for linking these inputs into the
Avalonia apphost and packing the resulting templates.

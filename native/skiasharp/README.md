# SkiaSharp Conan recipe

This Conan 2 recipe builds the SkiaSharp and HarfBuzzSharp static libraries for
Windows, Linux glibc/musl and macOS x64/arm64. It supports the SkiaSharp versions
in [`versions.json`](versions.json) (one SkiaSharp 3 and one SkiaSharp 4 version,
each with its mono/skia commit and HarfBuzzSharp version) and deploys to
`artifacts/skiasharp/<version>/<rid>/`. The deployer replaces that directory, so it
holds exactly the payload that `manifest.json` hashes. Pass the version with
`--version`; Task's `SKIASHARP_VERSION` defaults to `SkiaSharpVersion` in
`Directory.Build.props`.

The GN arguments and project settings follow the build files of the mono/SkiaSharp tag.
[`upstream-build.json`](upstream-build.json) records, for each version in `versions.json`, the git blob
SHAs of the upstream build files the recipe was checked against.

SkiaSharp 4 also packages the separate `jsonreader` archive required by Skottie.
Linux SkiaSharp 4 includes the matching target `cxx/libc++.a` archive.

`package()` writes `licenses/<component>/` before it hashes the payload into
`manifest.json`: Skia's `LICENSE`, HarfBuzz's `COPYING`, and the license files of every
`third_party/externals` checkout compiled into the archives. GN writes each object
below `obj/` at the directory of its source, so the object trees of the build name
those checkouts for the version, platform and GN arguments; `_external_wrappers` adds
the Skia directories that compile a header-only external. `_external_licenses` lists
the license files of each checkout at the revision that Skia's `DEPS` pins. Packaging
fails when the objects include a third-party directory without an entry, or a listed
file is missing; add the entry when an upgrade compiles a new external. The libc++
license of the Linux SkiaSharp 4 packages is in
[`src/package-skiasharp-static/licenses`](../../src/package-skiasharp-static/licenses).

On Windows or macOS, run the matching RID with MSVC/LLVM or Command Line Tools:

```sh
task build-skiasharp RID=win-x64
task pack-skia-static RID=win-x64
```

On macOS, use `RID=osx-x64` or `RID=osx-arm64` with the same tasks.

For Linux, build the matching cross image on an amd64 host. The sysroot directory
is `x64` or `arm64`; the image supplies LLVM, target C++ runtimes and fontconfig. The
versions of Task and uv in the image are build arguments from their pins, which
`python scripts/upstream.py build-args` prints.

```sh
docker build $(python scripts/upstream.py build-args) -f native/skiasharp/linux-musl-arm64.Dockerfile -t hostforge-skia .
docker run --rm -v "$PWD:/work" hostforge-skia task build-skiasharp RID=linux-musl-arm64 SKIASHARP_VERSION=<version> SYSROOT=/crossrootfs/arm64 JOBS=4
task pack-skia-static RID=linux-musl-arm64 SKIASHARP_VERSION=<version>
```

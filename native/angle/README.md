# ANGLE Conan recipe

This Conan 2 recipe owns ANGLE source checkout, patches, GN/Ninja
configuration, and the two complete static libraries. The custom deployer restores
the package to the artifact layout consumed by the existing MSBuild projects.

Build from the repository root (Git for Windows is required):

```powershell
task build-angle
task build-angle ARCH=arm64
```

The Conan package contains `libANGLE_static.lib`, `libGLESv2_static.lib`,
`av_libglesv2.def` and the license files of the code in the libraries; the deployer
copies them to `artifacts/angle/<version>/<rid>/`, the licenses into
`licenses/<component>/`. MSBuild remains responsible for linking these inputs into
the Avalonia apphost and packing the resulting templates.

## Licenses

`package()` reads the member paths of both archives. GN places each object below the
directory of the `BUILD.gn` that defines its target, so a member path names the
third-party code it comes from. `_third_party_licenses` maps those path prefixes to
the license file that the component's `README.chromium` names, at the pinned commit;
the root `BUILD.gn` defines `xxhash`, whose path has no `third_party` directory. The
recipe copies ANGLE's `LICENSE`, Chromium's license for the Chromium code in
`src/common/base` (from the `buildtools` checkout), and the license of every mapped
component found in the archives. It fails when a member below a `third_party`
directory has no entry or a license file is missing. When an upgrade links new
third-party code, add its object directory and license file to the table.

## Pinned sources

[`conandata.yml`](conandata.yml) pins both checkouts:

- `sources.commit`: the ANGLE commit that is built. `sources.branch` only records
  the Chromium branch the commit belongs to. The `inputs` group of the upstream
  workflow moves the commit to the branch head, together with `depot_tools.commit`,
  and opens a pull request; a branch change is only reported.
- `depot_tools.commit`: must equal the `third_party/depot_tools` revision in ANGLE's
  `DEPS` at the pinned commit. `source()` fails when they differ.

The recipe fetches depot_tools itself at that commit, so no depot_tools installation
is needed. It sets `DEPOT_TOOLS_UPDATE=0` to keep the pinned checkout and runs
`bootstrap/win_tools.bat` explicitly, because that setting also skips the bootstrap
that `gclient` would otherwise run on first use.

## Upgrading ANGLE

New commits on the pinned branch arrive as upstream pull requests (see
[DEVELOPMENT.md](../../DEVELOPMENT.md#上游跟踪与版本守卫)). To move to another
branch, follow the `Avalonia.Angle.Windows.Natives` version that Avalonia depends on
(`task upstream-check` lists it), then:

1. Set `sources.branch` and `sources.commit` in `conandata.yml`.
2. Set `depot_tools.commit` to the revision in ANGLE's `DEPS` at that commit.
3. Set `AngleVersion` in `Directory.Build.props` and `version` in
   [`conanfile.py`](conanfile.py) to the same value.
4. Update `AnglePackageVersion` and `_Major` as the package lock requires, then run
   `task upstream-lock` (see [DEVELOPMENT.md](../../DEVELOPMENT.md#上游跟踪与版本守卫)).
5. Check that the patches in `patches/` still apply.
6. Check that no new root `BUILD.gn` target compiles third-party sources into the
   archives without a `_third_party_licenses` entry (see [Licenses](#licenses)).

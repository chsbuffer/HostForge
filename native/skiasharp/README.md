# SkiaSharp Conan recipe

This Conan 2 recipe builds the SkiaSharp and HarfBuzzSharp static libraries for
Windows and Linux. The Windows HarfBuzz project is instantiated from
`scripts/libHarfBuzzSharp.vcxproj.in`; the upstream template remains byte-for-byte
unchanged in its original location.

The deployer restores the package libraries below
`artifacts/skiasharp/3.119.4/<rid>` for the existing MSBuild projects.

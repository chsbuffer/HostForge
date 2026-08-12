# HostLibs Conan recipe

This Conan 2 recipe builds and packages the .NET Runtime apphost and single-file
host static link inputs. `pgo=True` is the default package; set `pgo=False` for
the Windows no-PGO flavor.

Set `CONAN_HOME` to an absolute path (e.g. `$PWD/build/conan`). The
Runtime build has deeply nested paths and long MSVC command lines.

The deployer preserves the response-file-relative directory tree expected by the
existing MSBuild targets and writes it below
`artifacts/hostlibs/10.0.10/<flavor>/<rid>`.

#!/usr/bin/env bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  sysroot-util.sh pack <sysroot>
  sysroot-util.sh unpack <tar_path> <out_path>
EOF
}

die() {
    echo "error: $*" >&2
    exit 1
}

require_dir() {
    local path="$1"
    [[ -d "$path" ]] || die "directory not found: $path"
}

require_file() {
    local path="$1"
    [[ -f "$path" ]] || die "file not found: $path"
}

pack() {
    local sysroot="$1"
    require_dir "$sysroot"

    local sysroot_abs
    sysroot_abs="$(realpath "$sysroot")"

    local parent_dir
    parent_dir="$(dirname "$sysroot_abs")"

    local sysroot_name
    sysroot_name="$(basename "$sysroot_abs")"

    local tarball_path="${parent_dir}/${sysroot_name}.tar.xz"

    tar \
        --owner=0 \
        --group=0 \
        --numeric-owner \
        --sort=name \
        --no-xattrs \
        -I "xz -z9 -T0 --lzma2='dict=256MiB'" \
        -cf "$tarball_path" \
        -C "$sysroot_abs" \
        .

    echo "$tarball_path"
}

unpack() {
    local tar_path="$1"
    local out_path="$2"

    require_file "$tar_path"
    mkdir -p "$out_path"

    local tar_abs
    tar_abs="$(realpath "$tar_path")"

    local out_abs
    out_abs="$(realpath "$out_path")"

    tar mxf "$tar_abs" -C "$out_abs"
}

main() {
    [[ $# -ge 1 ]] || {
        usage
        exit 1
    }

    local command="$1"
    shift

    case "$command" in
        pack)
            [[ $# -eq 1 ]] || die "pack expects 1 argument"
            pack "$1"
            ;;
        unpack)
            [[ $# -eq 2 ]] || die "unpack expects 2 arguments"
            unpack "$1" "$2"
            ;;
        -h|--help|help)
            usage
            ;;
        *)
            usage
            die "unknown command: $command"
            ;;
    esac
}

main "$@"

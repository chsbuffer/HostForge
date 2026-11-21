# IMAGE is the official Linux build container of a runtime and RID (native/hostlibs/versions.json): an Azure Linux
# amd64 host with the upstream LLVM and the rootfs of the target in /crossrootfs/<arch>, Ubuntu for glibc and
# Alpine for musl.
ARG IMAGE

# HostLibs build: the upstream container with the tools that run Taskfile.yml.
FROM ${IMAGE} AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /uvx /usr/local/bin/
SHELL ["/bin/bash", "-euo", "pipefail", "-c"]
RUN curl -fLsS --retry 3 --retry-all-errors --connect-timeout 30 --max-time 180 \
      https://github.com/go-task/task/releases/download/v3.52.0/task_linux_amd64.tar.gz \
    | tar xz -C /usr/local/bin task
ENV UV_PYTHON=3.11 CONAN_HOME=/work/build/conan
WORKDIR /work

# Avalonia AppHost link of RID: Avalonia links -lfontconfig, so add fontconfig to the rootfs from the packages and
# checksums of the SkiaSharp image of the RID (on Alpine with its runtime dependency closure).
FROM build AS link
ARG RID
COPY native/skiasharp/fontconfig-*.sha256 /fontconfig/
RUN case "$RID" in \
      linux-x64) arch=x64 release=ubuntu18.04 checksums=fontconfig-ubuntu18.04-amd64.sha256 \
        packages=https://archive.ubuntu.com/ubuntu/pool/main/f/fontconfig ;; \
      linux-musl-x64) arch=x64 release=alpine3.17 checksums=fontconfig-alpine3.17-x86_64.sha256 \
        packages=https://dl-cdn.alpinelinux.org/alpine/v3.17/main/x86_64 ;; \
      linux-musl-arm64) arch=arm64 release=alpine3.17 checksums=fontconfig-alpine3.17-aarch64.sha256 \
        packages=https://dl-cdn.alpinelinux.org/alpine/v3.17/main/aarch64 ;; \
      *) echo "Unsupported RID '$RID'"; exit 1 ;; \
    esac; \
    rootfs="/crossrootfs/$arch"; \
    found=$(. "$rootfs/etc/os-release"; echo "$ID$VERSION_ID"); \
    [[ "$found" == "$release" || "$found" == "$release".* ]] \
      || { echo "The fontconfig checksums are for $release; the rootfs is $found"; exit 1; }; \
    cd /fontconfig; \
    while read -r checksum package; do \
      curl -fLsS --retry 3 --retry-all-errors --connect-timeout 30 --max-time 180 "$packages/$package" -o "$package"; \
      echo "$checksum  $package" | sha256sum -c -; \
      case "$package" in \
        *.apk) tar xzf "$package" --exclude='.*' -C "$rootfs" ;; \
        *.deb) llvm-ar x "$package"; tar xf data.tar.* -C "$rootfs"; rm data.tar.* control.tar.* debian-binary ;; \
      esac; \
      rm "$package"; \
    done < "$checksums"; \
    cd /; rm -r /fontconfig

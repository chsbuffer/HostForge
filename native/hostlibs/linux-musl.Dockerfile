# IMAGE is the official linux-musl build container of a runtime (native/hostlibs/versions.json): an Azure Linux
# amd64 host with the upstream LLVM and the Alpine rootfs of the target in /crossrootfs/<ARCH>.
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

# Avalonia AppHost link: Avalonia links -lfontconfig, so add fontconfig and its runtime dependency closure to the
# rootfs from the Alpine release and checksums of the SkiaSharp musl images.
FROM build AS link
ARG ARCH
COPY native/skiasharp/fontconfig-alpine3.17-*.sha256 /fontconfig/
RUN case "$ARCH" in x64) apk_arch=x86_64 ;; arm64) apk_arch=aarch64 ;; *) echo "Unsupported ARCH '$ARCH'"; exit 1 ;; esac; \
    rootfs="/crossrootfs/$ARCH"; \
    grep -Eq '^3\.17\.' "$rootfs/etc/alpine-release" \
      || { echo "The fontconfig checksums are for Alpine 3.17; the rootfs is $(cat "$rootfs/etc/alpine-release")"; exit 1; }; \
    cd /fontconfig; \
    while read -r checksum package; do \
      curl -fLsS --retry 3 --retry-all-errors --connect-timeout 30 --max-time 180 \
        "https://dl-cdn.alpinelinux.org/alpine/v3.17/main/$apk_arch/$package" -o "$package"; \
      echo "$checksum  $package" | sha256sum -c -; \
      tar xzf "$package" --exclude='.*' -C "$rootfs"; \
      rm "$package"; \
    done < "fontconfig-alpine3.17-$apk_arch.sha256"; \
    cd /; rm -r /fontconfig

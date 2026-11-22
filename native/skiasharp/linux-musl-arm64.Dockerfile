# Task and uv drive the build without changing its output. Their versions are build arguments from their
# pins (python scripts/upstream.py build-args), so a tool update changes neither this file nor the native
# cache keys.
ARG UV_VERSION
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# The target rootfs is Alpine 3.17; the Azure Linux amd64 host supplies LLVM 20.1.8.
FROM mcr.microsoft.com/dotnet-buildtools/prereqs@sha256:94db26dcf489e867faf22269fc5a79598f758135a9353c9c7806bb58594cab90
ARG TASK_VERSION
COPY --from=uv /uv /uvx /usr/local/bin/
SHELL ["/bin/bash", "-euo", "pipefail", "-c"]
RUN : "${TASK_VERSION:?pass the arguments of python scripts/upstream.py build-args}"; \
    curl -fLsS --retry 3 --retry-all-errors --connect-timeout 30 --max-time 180 \
      "https://github.com/go-task/task/releases/download/v$TASK_VERSION/task_linux_amd64.tar.gz" \
    | tar xz -C /usr/local/bin task
# Match fontconfig and its runtime dependency closure to the Alpine 3.17 rootfs.
COPY native/skiasharp/fontconfig-alpine3.17-aarch64.sha256 /fontconfig/packages.sha256
RUN cd /fontconfig; \
    while read -r checksum package; do \
      curl -fLsS --retry 3 --retry-all-errors --connect-timeout 30 --max-time 180 \
        "https://dl-cdn.alpinelinux.org/alpine/v3.17/main/aarch64/$package" -o "$package"; \
      echo "$checksum  $package" | sha256sum -c -; \
      tar xzf "$package" --exclude='.*' -C /crossrootfs/arm64; \
      rm "$package"; \
    done < packages.sha256; rm packages.sha256; cd /; rmdir /fontconfig
ENV UV_PYTHON=3.11 CONAN_HOME=/work/build/conan ROOTFS_DIR=/crossrootfs/arm64
WORKDIR /work

# Task and uv drive the build without changing its output. Their versions are build arguments from their
# pins (python scripts/upstream.py build-args), so a tool update changes neither this file nor the native
# cache keys.
ARG UV_VERSION
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# The target rootfs is Ubuntu 18.04; the Azure Linux host supplies LLVM 20.1.8.
FROM mcr.microsoft.com/dotnet-buildtools/prereqs@sha256:d9b516fb5cf3dab37d49828983cda87e88834e23973a8dcc941ffa63808417bd
ARG TASK_VERSION
COPY --from=uv /uv /uvx /usr/local/bin/
SHELL ["/bin/bash", "-euo", "pipefail", "-c"]
RUN : "${TASK_VERSION:?pass the arguments of python scripts/upstream.py build-args}"; \
    curl -fLsS --retry 3 --retry-all-errors --connect-timeout 30 --max-time 180 \
      "https://github.com/go-task/task/releases/download/v$TASK_VERSION/task_linux_amd64.tar.gz" \
    | tar xz -C /usr/local/bin task
# Keep the target rootfs usable after extraction to a consumer's build directory.
RUN test "$(readlink /crossrootfs/arm64/lib)" = /crossrootfs/arm64/usr/lib && \
    ln -sfn usr/lib /crossrootfs/arm64/lib
# Match fontconfig to the rootfs, using the upstream v4.153.1 checksums.
COPY native/skiasharp/fontconfig-ubuntu18.04-arm64.sha256 /fontconfig/packages.sha256
RUN cd /fontconfig; \
    while read -r checksum package; do \
      curl -fLsS "https://ports.ubuntu.com/ubuntu-ports/pool/main/f/fontconfig/$package" -o "$package"; \
      echo "$checksum  $package" | sha256sum -c -; \
      ar x "$package"; tar xf data.tar.* -C /crossrootfs/arm64; \
      rm -f "$package" data.tar.* control.tar.* debian-binary; \
    done < packages.sha256; rm packages.sha256; cd /; rmdir /fontconfig
ENV UV_PYTHON=3.11 CONAN_HOME=/work/build/conan ROOTFS_DIR=/crossrootfs/arm64
WORKDIR /work

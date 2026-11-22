# Task and uv drive the build without changing its output. Their versions are build arguments from their
# pins (python scripts/upstream.py build-args), so a tool update changes neither this file nor the native
# cache keys.
ARG UV_VERSION
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# The target rootfs is Ubuntu 18.04; the Azure Linux host supplies LLVM 20.1.8.
FROM mcr.microsoft.com/dotnet-buildtools/prereqs@sha256:0e88b78d527715d82ee6a958e44d296e4b09f6cc5792d9c97c95733d1f55e858
ARG TASK_VERSION
COPY --from=uv /uv /uvx /usr/local/bin/
SHELL ["/bin/bash", "-euo", "pipefail", "-c"]
RUN : "${TASK_VERSION:?pass the arguments of python scripts/upstream.py build-args}"; \
    curl -fLsS --retry 3 --retry-all-errors --connect-timeout 30 --max-time 180 \
      "https://github.com/go-task/task/releases/download/v$TASK_VERSION/task_linux_amd64.tar.gz" \
    | tar xz -C /usr/local/bin task
# Keep the target rootfs usable after extraction to a consumer's build directory.
RUN test "$(readlink /crossrootfs/x64/lib)" = /crossrootfs/x64/usr/lib && \
    ln -sfn usr/lib /crossrootfs/x64/lib
# Match fontconfig to the rootfs, using the upstream v4.153.1 checksums.
COPY native/skiasharp/fontconfig-ubuntu18.04-amd64.sha256 /fontconfig/packages.sha256
RUN cd /fontconfig; \
    while read -r checksum package; do \
      curl -fLsS "https://archive.ubuntu.com/ubuntu/pool/main/f/fontconfig/$package" -o "$package"; \
      echo "$checksum  $package" | sha256sum -c -; \
      ar x "$package"; tar xf data.tar.* -C /crossrootfs/x64; \
      rm -f "$package" data.tar.* control.tar.* debian-binary; \
    done < packages.sha256; rm packages.sha256; cd /; rmdir /fontconfig
ENV UV_PYTHON=3.11 CONAN_HOME=/work/build/conan
WORKDIR /work

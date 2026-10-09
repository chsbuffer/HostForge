# The target rootfs is Alpine 3.17; the Azure Linux amd64 host supplies LLVM 20.1.8.
FROM mcr.microsoft.com/dotnet-buildtools/prereqs@sha256:ee3b539632cf0acb3c7cbb9eac0e9d9fe28afc407b311cca9a5eca0a1fd29f2e
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /uvx /usr/local/bin/
SHELL ["/bin/bash", "-euo", "pipefail", "-c"]
RUN curl -fLsS https://github.com/go-task/task/releases/download/v3.52.0/task_linux_amd64.tar.gz \
    | tar xz -C /usr/local/bin task
# Match fontconfig and its runtime dependency closure to the Alpine 3.17 rootfs.
COPY native/skiasharp/fontconfig-alpine3.17-x86_64.sha256 /fontconfig/packages.sha256
RUN cd /fontconfig; \
    while read -r checksum package; do \
      curl -fLsS --retry 3 --retry-all-errors --connect-timeout 30 --max-time 180 \
        "https://dl-cdn.alpinelinux.org/alpine/v3.17/main/x86_64/$package" -o "$package"; \
      echo "$checksum  $package" | sha256sum -c -; \
      tar xzf "$package" --exclude='.*' -C /crossrootfs/x64; \
      rm "$package"; \
    done < packages.sha256; rm packages.sha256; cd /; rmdir /fontconfig
ENV UV_PYTHON=3.11 CONAN_HOME=/work/build/conan ROOTFS_DIR=/crossrootfs/x64
WORKDIR /work

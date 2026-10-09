# The target rootfs is Ubuntu 18.04; the Azure Linux host supplies LLVM 20.1.8.
FROM mcr.microsoft.com/dotnet-buildtools/prereqs@sha256:0e88b78d527715d82ee6a958e44d296e4b09f6cc5792d9c97c95733d1f55e858
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /uvx /usr/local/bin/
SHELL ["/bin/bash", "-euo", "pipefail", "-c"]
# Keep the target rootfs usable after extraction to a consumer's build directory.
RUN test "$(readlink /crossrootfs/x64/lib)" = /crossrootfs/x64/usr/lib && \
    ln -sfn usr/lib /crossrootfs/x64/lib
RUN curl -fLsS https://github.com/go-task/task/releases/download/v3.52.0/task_linux_amd64.tar.gz \
    | tar xz -C /usr/local/bin task
# Match fontconfig to the rootfs, using the upstream v4.153.1 checksums.
RUN mkdir /fontconfig && cd /fontconfig && \
    for pair in \
      'libfontconfig1-dev ce4d63624128b06eef6ea0f2d04f0baa1cba22d7325fcbcce48ce5cbe2533b1a' \
      'libfontconfig1 647bb8f09e751a39d488b0260db6015ce1118c56114cc771dd5bf907180f0c80'; do \
      read -r package checksum <<< "$pair"; \
      curl -fLsS "https://archive.ubuntu.com/ubuntu/pool/main/f/fontconfig/${package}_2.12.6-0ubuntu2_amd64.deb" -o package.deb; \
      echo "$checksum  package.deb" | sha256sum -c -; \
      ar x package.deb; tar xf data.tar.* -C /crossrootfs/x64; \
      rm -f package.deb data.tar.* control.tar.* debian-binary; \
    done && rmdir /fontconfig
ENV UV_PYTHON=3.11 CONAN_HOME=/work/build/conan
WORKDIR /work

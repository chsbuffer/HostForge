# The target rootfs is Ubuntu 18.04; the Azure Linux host supplies LLVM 20.1.8.
FROM mcr.microsoft.com/dotnet-buildtools/prereqs@sha256:d9b516fb5cf3dab37d49828983cda87e88834e23973a8dcc941ffa63808417bd
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /uvx /usr/local/bin/
SHELL ["/bin/bash", "-euo", "pipefail", "-c"]
# Keep the target rootfs usable after extraction to a consumer's build directory.
RUN test "$(readlink /crossrootfs/arm64/lib)" = /crossrootfs/arm64/usr/lib && \
    ln -sfn usr/lib /crossrootfs/arm64/lib
RUN curl -fLsS https://github.com/go-task/task/releases/download/v3.52.0/task_linux_amd64.tar.gz \
    | tar xz -C /usr/local/bin task
# Match fontconfig to the rootfs, using the upstream v4.153.1 checksums.
RUN mkdir /fontconfig; cd /fontconfig; \
    for pair in \
      'libfontconfig1-dev 93fc9ec2f69a87cbbc2efe7e5f3769c56ea9453a1449f28d78f192062b532975' \
      'libfontconfig1 de5942257801f0d344cc55c95cc190aae4e434b3d8d0d77a029f210da755fdc3'; do \
      read -r package checksum <<< "$pair"; \
      curl -fLsS "https://ports.ubuntu.com/ubuntu-ports/pool/main/f/fontconfig/${package}_2.12.6-0ubuntu2_arm64.deb" -o package.deb; \
      echo "$checksum  package.deb" | sha256sum -c -; \
      ar x package.deb; tar xf data.tar.* -C /crossrootfs/arm64; \
      rm -f package.deb data.tar.* control.tar.* debian-binary; \
    done; cd /; rmdir /fontconfig
ENV UV_PYTHON=3.11 CONAN_HOME=/work/build/conan ROOTFS_DIR=/crossrootfs/arm64
WORKDIR /work

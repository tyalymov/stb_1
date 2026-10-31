# Development image for longpole: the toolchain its tests drive, pinned.
#
# The tests configure and build small CMake projects, so the image carries
# C and C++ compilers, Ninja and one CMake release with the instrumentation
# API. Python is managed by uv at `uv sync` time and is not installed here.
FROM mcr.microsoft.com/devcontainers/base:ubuntu-24.04

ARG CMAKE_VERSION=4.4.5

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential clang ninja-build \
    && rm -rf /var/lib/apt/lists/*

# Official Kitware release, checked against the SHA-256 list published with it.
RUN set -eu; \
    case "$(uname -m)" in \
        x86_64) arch=x86_64 ;; \
        aarch64) arch=aarch64 ;; \
        *) echo "unsupported architecture: $(uname -m)" >&2; exit 1 ;; \
    esac; \
    archive="cmake-${CMAKE_VERSION}-linux-${arch}.tar.gz"; \
    release="https://github.com/Kitware/CMake/releases/download/v${CMAKE_VERSION}"; \
    cd /tmp; \
    curl -fsSL -o "${archive}" "${release}/${archive}"; \
    curl -fsSL -o SHA-256.txt "${release}/cmake-${CMAKE_VERSION}-SHA-256.txt"; \
    grep " ${archive}\$" SHA-256.txt | sha256sum -c -; \
    mkdir -p /opt/cmake; \
    tar -xzf "${archive}" -C /opt/cmake --strip-components=1; \
    rm -f "${archive}" SHA-256.txt
ENV PATH="/opt/cmake/bin:${PATH}"

COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /uvx /usr/local/bin/

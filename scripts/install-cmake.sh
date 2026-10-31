#!/usr/bin/env bash
# Install one official CMake release into a local tools directory.
#
# Usage: scripts/install-cmake.sh <version> [<tools-dir>]
#   <version>    release to install, e.g. 4.3.6 or 4.5.0-rc2
#   <tools-dir>  where releases live, default .tools/cmake (git-ignored)
#
# Prints the directory that contains the `cmake` and `ctest` binaries, so
# callers can do: CMAKE_BIN=$(scripts/install-cmake.sh 4.4.5).
#
# Binaries come from Kitware's GitHub releases and are checked against the
# SHA-256 list published next to them. A second run with the same version
# is a no-op. Works on Linux (x86_64, aarch64) and macOS (universal).
set -euo pipefail

version="${1:?usage: $0 <version> [<tools-dir>]}"
tools_dir="${2:-.tools/cmake}"
install_dir="${tools_dir}/${version}"

case "$(uname -s)" in
    Darwin) platform="macos-universal"; bin_rel="CMake.app/Contents/bin" ;;
    Linux)
        case "$(uname -m)" in
            x86_64)          platform="linux-x86_64" ;;
            aarch64 | arm64) platform="linux-aarch64" ;;
            *) echo "unsupported Linux architecture: $(uname -m)" >&2; exit 1 ;;
        esac
        bin_rel="bin" ;;
    *) echo "unsupported platform: $(uname -s)" >&2; exit 1 ;;
esac

archive="cmake-${version}-${platform}.tar.gz"
release_url="https://github.com/Kitware/CMake/releases/download/v${version}"
bin_dir="${install_dir}/cmake-${version}-${platform}/${bin_rel}"

if [[ -x "${bin_dir}/cmake" ]]; then
    echo "${bin_dir}"
    exit 0
fi

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

curl -fsSL --retry 3 -o "${tmp}/${archive}" "${release_url}/${archive}"
curl -fsSL --retry 3 -o "${tmp}/SHA-256.txt" "${release_url}/cmake-${version}-SHA-256.txt"

expected="$(grep " ${archive}\$" "${tmp}/SHA-256.txt" | cut -d' ' -f1)"
if [[ -z "${expected}" ]]; then
    echo "no SHA-256 entry for ${archive} in the release manifest" >&2
    exit 1
fi
if command -v sha256sum >/dev/null; then
    actual="$(sha256sum "${tmp}/${archive}" | cut -d' ' -f1)"
else
    actual="$(shasum -a 256 "${tmp}/${archive}" | cut -d' ' -f1)"
fi
if [[ "${actual}" != "${expected}" ]]; then
    echo "checksum mismatch for ${archive}: expected ${expected}, got ${actual}" >&2
    exit 1
fi

mkdir -p "${install_dir}"
tar -xzf "${tmp}/${archive}" -C "${install_dir}"

"${bin_dir}/cmake" --version | head -1 >&2
echo "${bin_dir}"

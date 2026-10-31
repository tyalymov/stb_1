#!/usr/bin/env sh
# Re-download the instrumentation JSON schemas from the CMake repository.
#
#   scripts/refresh-schemas.sh [tag]
#
# Fetches query/index/snippet-v1-schema.json for the given CMake tag (default:
# the one recorded in src/longpole/cmake/schemas/VERSION) and records the tag.
# Commit the result; the validator loads the vendored copies, never the network.
set -eu

schemas_dir="$(cd "$(dirname "$0")/.." && pwd)/src/longpole/cmake/schemas"
tag="${1:-$(cat "$schemas_dir/VERSION")}"
base="https://gitlab.kitware.com/cmake/cmake/-/raw/$tag/Help/manual/instrumentation"

for name in query index snippet; do
    file="$name-v1-schema.json"
    curl --silent --show-error --fail --location --output "$schemas_dir/$file" "$base/$file"
    echo "fetched $file @ $tag"
done
curl --silent --show-error --fail --location --output "$schemas_dir/CMAKE-LICENSE.txt" \
    "https://gitlab.kitware.com/cmake/cmake/-/raw/$tag/LICENSE.rst"
echo "fetched LICENSE.rst @ $tag"
printf '%s\n' "$tag" > "$schemas_dir/VERSION"

# Vendored CMake instrumentation schemas

`query-v1-schema.json`, `index-v1-schema.json` and `snippet-v1-schema.json`
are copied unchanged from the CMake repository,
`Help/manual/instrumentation/`, at the tag recorded in `VERSION`. The
snippet schema accepts every v1 data version (1.0, 1.1 and 1.2) through a
`oneOf`, so one copy covers CMake 4.3 through 4.5.

Refresh with `scripts/refresh-schemas.sh [tag]` and commit the result. The
validator in `longpole.cmake.schema` reads these files from the installed
package and never touches the network.

The schemas are part of CMake and carry its BSD 3-Clause licence.

# Run dump format, version 2

A run dump is what `longpole capture` writes for one CMake instrumentation
index and what every other command reads. It is a directory (or a `.tar.zst`
of that directory) holding a manifest and the files CMake wrote, unchanged.
Nothing is normalised at capture time: the callback runs while CMake is about
to delete its data, so it copies and stops. Steps, graph and host description
are parsed out of `raw/` on every read, with the readers in `longpole.cmake`.

```
<run-id>/
  manifest.json      where, when and how the run was captured
  raw/
    index.json       the index exactly as CMake passed it to the callback
    snippets/        every snippet the index listed, unchanged
    fileapi/         the File API reply directory, copied after a configure
```

Run ids are time-ordered: `YYYYMMDDTHHMMSSZ-xxxxxx`, UTC capture time plus six
random hex digits.

## manifest.json

| Field | Type | Meaning |
|-------|------|---------|
| `format_version` | integer | `2` for this layout. |
| `run_id` | string | Same as the directory name. |
| `created_at` | string | Capture time, ISO 8601, UTC. |
| `tool_version` | string | longpole version that wrote the dump. |
| `hook` | string | CMake hook that produced the index (`postGenerate`, `postCMakeBuild`, `postCTest`, ...). |
| `build_dir` | string | Build tree from the index, with symlinks resolved (`/private/var/...` rather than `/var/...` on macOS). |
| `environment` | object | See below. |
| `labels` | array of strings | What `enable --label` gave the callback. |

`environment` holds what is true only at capture time:

| Field | Type | Meaning |
|-------|------|---------|
| `cmake_version` | string or null | Version of the CMake that configured the tree, from its cache. |
| `generator` | string or null | Generator of the build tree. |
| `cpu_count` | integer or null | Logical CPUs seen by longpole at capture time. |
| `ccache` | object or null | `ccache --print-stats` as key/value strings. |

## raw/index.json and raw/snippets/

The index and its snippets, byte for byte, as described by the CMake
instrumentation API (`Help/manual/cmake-instrumentation.7.rst` in the CMake
sources). Snippet paths inside the copied index still name CMake's data
directory; readers resolve them against `raw/snippets/` instead.

Neither the callback nor the readers validate against the vendored schema:
a CMake newer than it writes fields and versions the schema forbids, and the
point of keeping raw files is that such a dump is still stored and still
reads, unknown snippet fields landing in `Step.extra`. The schema serves the
tests, which check longpole's reading of the format against CMake's own
description of it.

## raw/fileapi/

A copy of `<build>/.cmake/api/v1/reply/`, taken by the `postGenerate` hook
right after CMake rewrote it. It answers the query `enable` planted: codemodel
version 2 (targets, dependencies, sources, artifacts) and toolchains version 1.

Only a configure's run carries this directory. A build run has none, because
the launcher that records its steps was written into the build system by the
configure before it, and the graph did not change since. A reader therefore
takes the graph of a build from the newest run of the same `build_dir`,
captured no later than the build, that has `raw/fileapi/`. A build with no
such run, which happens when the tree was generated before recording was
enabled, has no graph until the next `cmake` run on the tree.

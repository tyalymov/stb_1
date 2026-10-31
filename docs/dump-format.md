# Run dump format, version 1

A run dump is what `longpole capture` writes for one CMake instrumentation
index and what every other command reads. It is a directory (or a `.tar.zst`
of that directory) with a fixed set of files. Field names are the ones in
`longpole.model`; this page is the contract for anyone reading a dump without
longpole.

```
<run-id>/
  manifest.json      where, when and how the run was captured
  steps.jsonl        one JSON object per step, index order
  graph.json         target dependency graph; absent when unavailable
  system.json        static host information; absent when not requested
  raw/
    index.json       the index exactly as CMake passed it to the callback
    snippets/        every snippet the index listed, unchanged
```

Run ids are time-ordered: `YYYYMMDDTHHMMSSZ-xxxxxx`, UTC capture time plus six
random hex digits.

## manifest.json

| Field | Type | Meaning |
|-------|------|---------|
| `format_version` | integer | `1` for this layout. |
| `run_id` | string | Same as the directory name. |
| `created_at` | string | Capture time, ISO 8601, UTC. |
| `tool_version` | string | longpole version that wrote the dump. |
| `hook` | string | CMake hook that produced the index (`postCMakeBuild`, `postCTest`, `manual`, ...). |
| `build_dir` | string | Build tree from the index, with symlinks resolved (`/private/var/...` rather than `/var/...` on macOS). |
| `data_version` | object | `{"major": 1, "minor": N}`; N is 0 for CMake 4.3, 1 for 4.4, 2 for 4.5. |
| `environment` | object | See below. |
| `labels` | array of strings | Free-form tags such as `ci:<run id>`. |

`environment`:

| Field | Type | Meaning |
|-------|------|---------|
| `cmake_version` | string or null | Version of the CMake that wrote the data. |
| `generator` | string or null | Generator of the build tree. |
| `cpu_count` | integer or null | Logical CPUs seen by longpole at capture time. |
| `git` | object or null | `{"sha", "branch", "dirty"}`; `branch` is null on a detached head. |
| `toolchains` | array | `{"language", "compiler_id", "compiler_path", "compiler_version"}` per language. |
| `ccache` | object or null | `ccache --print-stats` as key/value strings. |

## steps.jsonl

One line per step, in the order the index listed the snippets. Every field is
present on every line; fields a data version does not carry are `null`.

| Field | Type | From the snippet |
|-------|------|------------------|
| `role` | string | `role` |
| `command` | string or null | `command`; null for the `build` role |
| `working_dir` | string | `workingDir` |
| `result` | integer or null | `result` |
| `time_start_ms` | integer | `timeStart`, milliseconds since the epoch |
| `duration_ms` | integer | `duration`, milliseconds |
| `target` | string or null | `target` |
| `target_labels` | array of strings | `targetLabels` |
| `outputs` | array of strings | `outputs`, relative to `working_dir` |
| `output_sizes` | array of integers | `outputSizes`, bytes, same order as `outputs` |
| `source` | string or null | `source` |
| `language` | string or null | `language` |
| `config` | string or null | `config` |
| `test_name` | string or null | `testName` |
| `show_only` | boolean or null | `showOnly` |
| `cmake_content` | string or null | `cmakeContent` |
| `trace_file` | string or null | `traceFile` (data 1.1+) |
| `stdout` | string or null | `stdout` (data 1.1+) |
| `stderr` | string or null | `stderr` (data 1.1+) |
| `interrupt_signal` | integer or null | `interruptSignal` (data 1.2+) |
| `process_metrics` | object or null | `processMetrics` (data 1.2+): `{"max_rss_kib", "user_time_us", "system_time_us"}` |
| `host_load` | object or null | `dynamicSystemInformation`: `{"memory_used_before_kib", "memory_used_after_kib", "load_average_before", "load_average_after"}` |
| `extra` | object | Snippet fields this longpole did not know, verbatim |

A snippet is one launched command. A static library produces two `link` steps
(`ar`, then `ranlib`); a custom command with two `COMMAND` entries produces two
`custom` steps. Readers group by `target` or by `outputs` before summing.

## graph.json

```json
{
  "source": "file-api",
  "targets": [{"id": "b::@6890...", "name": "b", "type": "SHARED_LIBRARY",
               "artifacts": ["libb.dylib"], "sources": ["/abs/path/gen_b.c"]}],
  "edges": [["c::@6890...", "b::@6890..."]]
}
```

An edge `[dependent, dependency]` means the dependent cannot be built before
the dependency. The File API reports the dependencies the generator enforces,
transitive link dependencies included, so some edges are implied by others.

## system.json

The typed subset of the index's `staticSystemInformation` (`os_name`,
`os_version`, `hostname`, `processor_name`, `logical_cpus`, `physical_cpus`,
`total_memory_mib`; each may be null) plus `raw`, the whole object as CMake
wrote it.

## raw/

The index and its snippets, byte for byte. CMake deletes the originals after
the callbacks return, so this copy is the only one. A newer longpole can
re-normalise a dump from `raw/` if `steps.jsonl` turns out to have dropped
something.

# Observed CMake instrumentation behaviour

The `cmake-instrumentation(7)` manual leaves several practical questions
open. This page records what CMake actually does, as pinned by
`tests/test_cmake_behaviors.py` and the fixture tests. When a new release
changes one of these, the test fails and this table gets a new row.

Observed on 2026-10-31, macOS 15 (arm64), Ninja generator, CMake 4.3.6,
4.4.4, 4.4.5 and 4.5.0-rc2. All rows held on every version unless noted.

| # | Question | Observation | What longpole does about it |
|---|----------|-------------|-----------------------------|
| 1 | What is one snippet? | One launched command. A custom command with two `COMMAND` entries yields two `custom` snippets. | Group steps by target and output, never assume one snippet per CMake-level step. |
| 2 | How many link snippets per target? | A static library is created by two commands, `ar` then `ranlib`, so it yields two `link` snippets. Shared libraries and executables yield one. | Same as above: a target's link time is the sum of its link snippets. |
| 3 | Where do configure and generate snippets go? | They are written before any hook fires and land in whichever index comes first. Without a `postGenerate` hook the `postCMakeBuild` index carries them. | Register `postGenerate` so configure time is its own run. |
| 4 | Does a query written after generate take effect on the next build? | Halfway. `cmake --build` reads the query, fires `postCMakeBuild` and runs the callback, but the index holds only a `cmakeBuild` snippet. Compile, link and custom steps appear only after a reconfigure, because the recording launcher is written into the build system at generate time. | An index whose only build-time snippet is `cmakeBuild` means the tree was generated before the query existed; `capture` says so and `doctor` checks for it. |
| 5 | Does CMake regenerate when a query appears? | No. The build output shows no re-run and no new configure or generate snippets. | Tell the user to reconfigure; nothing else works. |
| 6 | Does ctest need `CTEST_USE_INSTRUMENTATION`? | No. With a plain query, `ctest` writes one `ctest` snippet per run and one `test` snippet per test and fires `postCTest`. The variable is only for CDash dashboard mode. | Nothing to set; register `postCTest`. |
| 7 | What happens with hooks but no callbacks? | CMake indexes at the hook and deletes the index and snippets unread. | Never write a query with hooks and an empty callback list. |
| 8 | What happens with no hooks at all? | Snippets accumulate and nothing deletes them: twelve after the first chain build, one more `cmakeBuild` after a no-op rebuild. | This is the basis of a pull mode, if one is ever needed. |
| 9 | Which hooks fire inside `cmake --workflow`? | The per-step hooks fire as usual: `postGenerate`, `postCMakeBuild`, `postCTest` each get their own index. `postCMakeWorkflow` then fires with an empty index because every snippet was already consumed. | Treat each step as its own run; accept an index with no snippets without creating an empty run. |
| 10 | What does `cmake --install` record? | `postCMakeInstall` fires with a single `cmakeInstall` snippet. The per-script `install` role from the manual did not appear for a project with one install rule. | Expect install time at the `cmakeInstall` level only. |
| 11 | What does `postGenerate` carry? | Exactly two snippets: `configure` and `generate`. | Use it for configure-time reporting. |
| 12 | Which data version does each line write? | 4.3 writes 1.0, 4.4 writes 1.1, 4.5 (rc2) writes 1.2. The index carries the same version as the snippets. | The reader keys its handling on the snippet version, not on the CMake version string. |

To rerun the observations against every pinned release:

```sh
make cmake-tools
uv run pytest tests/test_cmake_behaviors.py
```

Only versions found under `.tools/cmake` and on `PATH` are exercised; the
`each_cmake` fixture in `tests/conftest.py` discovers them.

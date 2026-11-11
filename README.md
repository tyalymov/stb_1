# longpole

Find the long pole in your CMake build.

CMake 4.3 and later can record every step of a build: each configure,
compile, link, custom command and test, with its command line, duration,
exit status and the size of what it wrote. longpole switches that recording
on, stores each build as a run, and tells you what actually gated it: the
chain of dependent targets the build could not finish before, how many
cores were busy from moment to moment, the slowest translation units and
targets, and how big the results are. Your project does not change. There
is no wrapper around the build.

It works through CMake's instrumentation API. One query file tells CMake to
call `longpole capture` after each build, install and test run; the callback
copies what CMake recorded into a run before CMake deletes it; `longpole
analyze` reads the run back.

## Install

longpole is not on PyPI yet. Until the first release, install from the
repository:

```sh
uv tool install git+https://github.com/tyalymov/longpole
# or
pipx install git+https://github.com/tyalymov/longpole
# or, inside a virtual environment
pip install git+https://github.com/tyalymov/longpole
```

You need Python 3.11 or newer and CMake 4.3 or newer on the machine that
builds. `longpole doctor` checks both.

## Use

Turn recording on once, then build as you always do:

```sh
longpole enable build        # before you configure the tree

cmake -S . -B build -G Ninja
cmake --build build
```

Each build prints one line on standard error when it finishes:

```
longpole: postCMakeBuild recorded as 20261031T140557Z-5d3136: 100 snippets (longpole analyze)
```

Then look at it:

```sh
cd build
longpole analyze
```

Here is {fmt}, the formatting library, built from scratch with Ninja on a
16-core laptop (CMake 4.4, Release, tests included):

```
run 20261031T140557Z-5d3136  postCMakeBuild  2026-10-31T14:05:57.811652+00:00
build dir: /work/fmt/build
span  │ 8.3 s
busy  │ 1m 44s across the leaf steps
steps │ 100

phases
phase   ┃ steps ┃  total ┃ share
━━━━━━━━╇━━━━━━━╇━━━━━━━━╇━━━━━━
custom  │     2 │  1.9 s │    2%
compile │    53 │ 1m 40s │   96%
link    │    44 │  2.2 s │    2%

slowest targets
name                  ┃   time ┃ steps ┃ detail
━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━
format-impl-test      │ 10.3 s │     7 │ 6 compile, 1 link
scan-test             │  8.3 s │     6 │ 5 compile, 1 link
chrono-test           │  8.0 s │     2 │ 1 compile, 1 link
printf-test           │  7.5 s │     2 │ 1 compile, 1 link
unicode-test          │  7.3 s │     6 │ 5 compile, 1 link
format-test           │  6.9 s │     2 │ 1 compile, 1 link
no-builtin-types-test │  6.8 s │     6 │ 5 compile, 1 link
core-test             │  5.3 s │     2 │ 1 compile, 1 link
ranges-test           │  5.0 s │     3 │ 2 compile, 1 link
gtest                 │  4.9 s │     5 │ 1 compile, 4 link

slowest sources (under /work/fmt)
name                          ┃  time ┃ steps ┃ detail
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━━
test/gtest-extra.cc           │ 8.8 s │     6 │ C++
test/chrono-test.cc           │ 7.9 s │     1 │ C++
test/printf-test.cc           │ 7.4 s │     1 │ C++
test/util.cc                  │ 7.0 s │     6 │ C++
test/format-test.cc           │ 6.8 s │     1 │ C++
src/os.cc                     │ 6.4 s │     5 │ C++
test/core-test.cc             │ 5.2 s │     1 │ C++
test/gtest/gmock-gtest-all.cc │ 4.8 s │     1 │ C++
test/xchar-test.cc            │ 4.2 s │     1 │ C++
test/ranges-test.cc           │ 4.1 s │     1 │ C++

slowest custom commands
name    ┃   time ┃ steps ┃ detail
━━━━━━━━╇━━━━━━━━╇━━━━━━━╇━━━━━━━
fmt.pcm │  1.6 s │     1 │ passed
fmt.o   │ 216 ms │     1 │ passed

critical path: 8.0 s lower bound, 96% of the 8.3 s span
target      ┃ step    ┃  time ┃ what
━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
chrono-test │ compile │ 7.9 s │ /work/fmt/test/chrono-test.cc
chrono-test │ link    │ 42 ms │ bin/chrono-test

parallelism: peak 18 of 16 cores, average 12.5
active 8.3 s, idle 14 ms, single-step tail 304 ms (4%)
At its peak 18 steps ran at once on 16 cores (12.5 on average), so more jobs would not help on this
machine. 4% of the active time ran a single step.

artifacts: 59.0 MiB in 84 files
kind       ┃ files ┃     size
━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━
executable │    24 │ 19.7 MiB
library    │     5 │  1.3 MiB
object     │    53 │ 13.1 MiB
generated  │     2 │ 24.8 MiB

largest artifacts
path                                             ┃     size ┃ kind       ┃ target
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━
fmt.pcm                                          │ 24.5 MiB │ generated  │
bin/printf-test                                  │  1.6 MiB │ executable │ printf-test
bin/format-test                                  │  1.6 MiB │ executable │ format-test
test/CMakeFiles/format-test.dir/format-test.cc.o │  1.6 MiB │ object     │ format-test
test/CMakeFiles/printf-test.dir/printf-test.cc.o │  1.6 MiB │ object     │ printf-test
bin/chrono-test                                  │  1.5 MiB │ executable │ chrono-test
test/CMakeFiles/chrono-test.dir/chrono-test.cc.o │  1.5 MiB │ object     │ chrono-test
bin/module-test                                  │  1.4 MiB │ executable │ module-test
bin/core-test                                    │  1.3 MiB │ executable │ core-test
bin/xchar-test                                   │  1.1 MiB │ executable │ xchar-test
```

The critical path is the part worth reading first. It is the longest chain
of steps that have to wait for each other, priced from what the target
graph guarantees: a target's compiles wait only for generated sources, its
link waits for its own compiles and for the links of the targets it depends
on. However many cores the machine has, the build cannot finish before that
chain does. Here the chain is one file: `test/chrono-test.cc` compiles for
7.9 s of the 8.3 s build, so more jobs would not help and splitting that
file would. When the bound is well below the span, the build was waiting
either for cores or for ordering the target graph does not record, such as
a custom command that depends on another target's output.

Other commands:

```sh
longpole runs                          # every run of this tree, oldest first
longpole runs --all --since 7d         # every tree, last week
longpole runs show latest~1            # the manifest of the run before the last
longpole runs prune --keep 20          # drop old runs
longpole doctor build                  # is this tree recording, and if not, why
longpole export --perfetto trace.json  # open the timeline in https://ui.perfetto.dev
longpole export --markdown run.md      # a snapshot for a CI job summary
longpole analyze --output-format json  # everything above as JSON
```

A run reference is an id or a prefix of one, `latest`, `latest~N`,
`sha:<commit prefix>`, `label:<name>`, or the path of a run directory.

## What a run holds

The files CMake wrote, unchanged: the index, one snippet per launched step
and, after a configure, the File API reply the target graph comes from.
Next to them a manifest with what is only knowable at capture time: the
hook, the tree, the CMake version and generator, the git state, ccache's
counters and your labels. The callback copies and stops; parsing happens
when a run is read, so a dump written by an older longpole still reads with
a newer one. The layout is described in
[docs/dump-format.md](docs/dump-format.md).

Runs live in `<build>/.longpole/` of the tree you enabled; `enable --store
PATH` puts them elsewhere. The store and any `enable --label NAME` are
written into the callback itself, so the callback never has to work out
where to write and a CI job can say once where its runs go and how they
are tagged:

```sh
longpole enable build --store "$RUNNER_TEMP/longpole" --label "ci:$GITHUB_RUN_ID"
```

## Limits

- CMake 4.3 or newer; older releases do not record anything. CMake 4.4
  adds captured output, 4.5 adds per-step CPU time and peak memory, and
  longpole reads whatever the installed version writes.
- Makefile, Ninja and FASTBuild generators. The instrumentation hooks into
  the command launcher those generators have; Visual Studio and Xcode have
  none, so CMake records nothing there.
- A tree configured before you ran `enable` records only the outer build
  step, and no target graph, until you run `cmake` on it again; `doctor`
  tells you when that is the case, and so does `analyze`.
- Within a target, compile steps and independent custom commands are taken
  to run in parallel and link steps one after another. That is exact for
  the usual shapes and never worse than the sum of the target's steps.
- Where the time inside a compiler goes (which header, which template) is
  not something CMake records, and longpole does not read compiler-specific
  traces.
- The recorded steps are what CMake launched. Work that happens outside
  them, such as `ccache` serving a hit, appears only as a short step.

Known behaviour of the instrumentation API across CMake releases, as
measured by this project's tests, is in
[docs/cmake-behaviors.md](docs/cmake-behaviors.md).

## Development

```sh
uv sync                 # environment with the pinned dependencies
make check              # lint, types, docstrings, tests
```

The tests drive real CMake builds of small fixture projects and pin what
each CMake release does, so they need CMake 4.3 or newer and a C and C++
toolchain; the dev container in `.devcontainer/` has both. `LONGPOLE_CMAKE`
selects a `cmake` that is not first on `PATH`, and CI runs the tests on
every release line. [CONTRIBUTING.md](CONTRIBUTING.md) has the rules for
code and docstrings. [docs/demo.md](docs/demo.md) explains how the demo
recording is made.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

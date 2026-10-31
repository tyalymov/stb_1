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
longpole enable --global     # every CMake build of this user on this machine
# or, for one build tree, before you configure it:
longpole enable build

cmake -S . -B build -G Ninja
cmake --build build
```

Each build prints one line on standard error when it finishes:

```
longpole: postCMakeBuild recorded as 20261031T051028Z-38635a: 10 steps, 1.5 s, critical path 1.3 s (89%) (longpole analyze)
```

Then look at it:

```sh
cd build
longpole analyze
```

Here is a small project of three targets in a strict chain, each built from
a generated source:

```
run 20261031T051028Z-38635a  postCMakeBuild  2026-10-31T05:10:28+00:00
build dir: /work/chain/build
span  │ 1.5 s
busy  │ 1.3 s across the leaf steps
steps │ 10

phases
phase   ┃ steps ┃ total ┃ share
━━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━
custom  │     3 │ 1.2 s │   87%
compile │     3 │ 81 ms │    6%
link    │     3 │ 97 ms │    7%

slowest custom commands
name    ┃   time ┃ steps ┃ detail
━━━━━━━━╇━━━━━━━━╇━━━━━━━╇━━━━━━━
gen_a.c │ 623 ms │     1 │ passed
gen_b.c │ 318 ms │     1 │ passed
gen_c.c │ 213 ms │     1 │ passed

critical path: 1.3 s lower bound, 89% of the 1.5 s span
target ┃  total ┃ custom ┃ compile ┃  link
━━━━━━━╇━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━
a      │ 682 ms │ 623 ms │   27 ms │ 32 ms
b      │ 377 ms │ 318 ms │   27 ms │ 32 ms
c      │ 273 ms │ 213 ms │   27 ms │ 33 ms

parallelism: peak 1 of 16 cores, average 1.0
active 1.3 s, idle 140 ms, single-step tail 1.3 s (100%)
At most 1 of 16 cores were busy at once (1.0 on average): either the job
count was below the core count, or there was no more independent work.
100% of the active time ran a single step.

artifacts: 83.2 KiB in 9 files
kind       ┃ files ┃     size
━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━
executable │     1 │ 32.6 KiB
library    │     2 │ 48.9 KiB
object     │     3 │  1.6 KiB
generated  │     3 │    119 B
```

The critical path is the part worth reading first. It is the longest chain
of targets that have to wait for each other, priced from the steps each
target ran. However many cores the machine has, the build cannot finish
before that chain does. When the chain is most of the span, as here, more
jobs will not help; when it is a small part, they will.

Other commands:

```sh
longpole runs                          # every run of this tree, oldest first
longpole runs --all --since 7d         # every tree, last week
longpole runs show latest~1            # the manifest of the run before the last
longpole runs prune --keep 20          # drop old runs
longpole doctor build                  # is this tree recording, and if not, why
longpole export --perfetto trace.json  # open the timeline in https://ui.perfetto.dev
longpole export --markdown run.md      # a snapshot for a CI job summary
longpole --json analyze                # everything above as JSON
```

A run reference is an id or a prefix of one, `latest`, `latest~N`,
`sha:<commit prefix>`, `label:<name>`, or the path of a run directory.

## What a run holds

Every step CMake launched, normalised and kept in order; the target graph
from CMake's File API, which is what the critical path is computed from;
the host description CMake recorded; the CMake version, generator, compiler
and git state at capture time; and the raw files CMake wrote, so a newer
longpole can re-read an old run. The layout is described in
[docs/dump-format.md](docs/dump-format.md).

Runs live in `<build>/.longpole/` for a tree you enabled, or in your user
data directory for `--global` (`~/.local/share/longpole` on Linux,
`~/Library/Application Support/longpole` on macOS). `--store PATH` or
`LONGPOLE_STORE` points everything at another directory.

## Limits

- CMake 4.3 or newer; older releases do not record anything. CMake 4.4
  adds captured output, 4.5 adds per-step CPU time and peak memory, and
  longpole reads whatever the installed version writes.
- Makefile, Ninja and FASTBuild generators. The instrumentation hooks into
  the command launcher those generators have; Visual Studio and Xcode have
  none, so CMake records nothing there.
- A tree configured before you ran `enable` records only the outer build
  step until you run `cmake` on it again; `doctor` tells you when that is
  the case, and so does the status line.
- The critical path needs the target graph, which CMake writes at configure
  time when asked. `enable build` asks before the first configure. With
  `--global`, the first build of a tree has no graph and the status line
  says so; the next configure fixes it for good.
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
make cmake-tools        # CMake 4.3, 4.4 and 4.5 under .tools/ for the test matrix
make check              # lint, types, docstrings, tests
```

The tests drive real CMake builds of small fixture projects and pin what
each CMake release does. [CONTRIBUTING.md](CONTRIBUTING.md) has the rules
for code and docstrings. [docs/demo.md](docs/demo.md) explains how the demo
recording is made.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

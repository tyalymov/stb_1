# Recording the demo

The README links to a short recording: a project builds, the status line
appears, `longpole analyze` shows the critical path. This is how to make it
again when the output changes.

## What is recorded

1. `longpole enable build` on a fresh tree of `tests/fixtures/chain`.
2. `cmake -S . -B build -G Ninja` and `cmake --build build`; the status line
   from the callback ends the build output.
3. `cd build && longpole analyze`, paused on the critical path table.
4. `longpole export --perfetto trace.json`, then the trace opened in
   https://ui.perfetto.dev (a screenshot, not part of the terminal recording).

Use a terminal 100 columns wide; the tables are laid out for that width.

## Tools

The recording is made with [vhs](https://github.com/charmbracelet/vhs),
which replays a script and writes a GIF, so two recordings of the same
script look the same. The script is `docs/demo.tape`:

```sh
brew install vhs            # or see the vhs README for other systems
cd docs && vhs demo.tape    # writes demo.gif next to it
```

The tape copies the fixture into a temporary directory and sets
`CMAKE_CONFIG_DIR` there, so a `--global` query on the recording machine does
not leak into the demo and the demo does not leave a query behind.

Keep the GIF under a couple of megabytes: 100 columns, 30 rows, a 1.5 s pause
on the analysis, and no idle time before the first command.

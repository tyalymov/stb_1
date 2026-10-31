# longpole

Build telemetry for CMake. longpole captures the instrumentation data that
CMake 4.3 and later can record for every configure, compile, link, custom
command and test step, stores each build as a self-contained run, and shows
what actually gated it: the critical path, how well the machine was used,
the slowest translation units and targets, and how all of that changed
between two runs.

Status: pre-alpha. Nothing here is ready to use yet.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

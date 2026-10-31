cmake_minimum_required(VERSION 4.3)
project(workflow C)

# The smallest project that exercises every step of `cmake --workflow`:
# one executable, one test, one install rule. CMakePresets.json defines the
# presets the workflow runs.

add_executable(hello hello.c)
enable_testing()
add_test(NAME hello_runs COMMAND hello)
install(TARGETS hello RUNTIME DESTINATION bin)

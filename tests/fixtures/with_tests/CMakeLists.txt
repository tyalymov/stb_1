cmake_minimum_required(VERSION 4.3)
project(with_tests NONE)

# No code to build: the point is ctest. Four tests with known durations,
# one of which fails on purpose, so a test run produces predictable test
# snippets without a compiler in the picture.

enable_testing()
add_test(NAME fast COMMAND ${CMAKE_COMMAND} -E sleep 0.1)
add_test(NAME medium COMMAND ${CMAKE_COMMAND} -E sleep 0.3)
add_test(NAME slow COMMAND ${CMAKE_COMMAND} -E sleep 0.5)
add_test(NAME failing COMMAND ${CMAKE_COMMAND} -E false)

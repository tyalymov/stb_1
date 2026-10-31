cmake_minimum_required(VERSION 4.3)
project(chain C)

# Three targets in a strict chain: a <- b <- c. Each target's only source is
# produced by generate.cmake, which sleeps for a known time first and whose
# custom command depends on the previous target's artifact. Nothing here can
# run in parallel, so the build really takes the three sleeps
# (0.6 + 0.3 + 0.2 seconds) plus three compiles and three links. The summary
# and utilization tests use those numbers as the expected answer.
#
# The critical path test does not: the target graph records that b depends
# on a, not that b's generator waits for a's artifact, so the lower bound
# longpole computes lets the generators start together and counts only the
# longest one. The real build is slower than the bound, which is the
# direction a lower bound may miss. See test_critical_path.py.
#
# The libraries are shared on purpose: a static library is created by two
# launched commands (ar, then ranlib) and therefore yields two link snippets,
# which would blur the one-step-per-target picture this fixture is for.

set(generate ${CMAKE_CURRENT_SOURCE_DIR}/generate.cmake)

add_custom_command(
    OUTPUT gen_a.c
    COMMAND ${CMAKE_COMMAND} -DSLEEP=0.6 -DINPUT=${CMAKE_CURRENT_SOURCE_DIR}/a.c.in
            -DOUTPUT=${CMAKE_CURRENT_BINARY_DIR}/gen_a.c -P ${generate}
    DEPENDS a.c.in ${generate}
    COMMENT "generating gen_a.c (sleep 0.6)"
)
add_library(a SHARED gen_a.c)

add_custom_command(
    OUTPUT gen_b.c
    COMMAND ${CMAKE_COMMAND} -DSLEEP=0.3 -DINPUT=${CMAKE_CURRENT_SOURCE_DIR}/b.c.in
            -DOUTPUT=${CMAKE_CURRENT_BINARY_DIR}/gen_b.c -P ${generate}
    DEPENDS b.c.in ${generate} a
    COMMENT "generating gen_b.c (sleep 0.3, after a)"
)
add_library(b SHARED gen_b.c)
target_link_libraries(b PUBLIC a)

add_custom_command(
    OUTPUT gen_c.c
    COMMAND ${CMAKE_COMMAND} -DSLEEP=0.2 -DINPUT=${CMAKE_CURRENT_SOURCE_DIR}/c.c.in
            -DOUTPUT=${CMAKE_CURRENT_BINARY_DIR}/gen_c.c -P ${generate}
    DEPENDS c.c.in ${generate} b
    COMMENT "generating gen_c.c (sleep 0.2, after b)"
)
add_executable(c gen_c.c)
target_link_libraries(c PRIVATE b)

install(TARGETS c RUNTIME DESTINATION bin)

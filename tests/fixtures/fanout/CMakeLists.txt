cmake_minimum_required(VERSION 4.3)
project(fanout C)

# One executable from eight independent generated sources. Every generator
# step sleeps 0.3 s and depends on nothing else, so with enough jobs the
# eight steps overlap and the build takes about one sleep instead of eight.
# This is the fixture for measuring parallelism and utilization.

set(generate ${CMAKE_CURRENT_SOURCE_DIR}/generate.cmake)
set(parts "")
foreach(i RANGE 0 7)
    set(output ${CMAKE_CURRENT_BINARY_DIR}/part${i}.c)
    file(WRITE ${CMAKE_CURRENT_BINARY_DIR}/part${i}.c.in "int part${i}(void) { return ${i}; }\n")
    add_custom_command(
        OUTPUT ${output}
        COMMAND ${CMAKE_COMMAND} -DSLEEP=0.3 -DINPUT=${CMAKE_CURRENT_BINARY_DIR}/part${i}.c.in
                -DOUTPUT=${output} -P ${generate}
        DEPENDS ${CMAKE_CURRENT_BINARY_DIR}/part${i}.c.in ${generate}
        COMMENT "generating part${i}.c (sleep 0.3)"
    )
    list(APPEND parts ${output})
endforeach()

add_executable(fanout main.c ${parts})

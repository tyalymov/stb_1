# Produce one generated source after a deliberate pause.
#
#   cmake -DSLEEP=<seconds> -DINPUT=<file> -DOUTPUT=<file> -P generate.cmake
#
# A custom command with two COMMAND entries is launched twice and so yields
# two instrumentation snippets. Routing both steps through this script keeps
# one snippet per generated file, which is what the tests count.
execute_process(COMMAND ${CMAKE_COMMAND} -E sleep ${SLEEP} COMMAND_ERROR_IS_FATAL ANY)
file(COPY_FILE ${INPUT} ${OUTPUT})

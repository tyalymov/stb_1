# vhs script for the README demo. Run from the docs directory: vhs demo.tape

Output demo.gif
Set Shell "bash"
Set Width 1100
Set Height 720
Set FontSize 16
Set TypingSpeed 40ms

Hide
Type "export DEMO=$(mktemp -d) && cp -r ../tests/fixtures/chain $DEMO/src && export CMAKE_CONFIG_DIR=$DEMO/cfg && cd $DEMO && clear"
Enter
Show

Type "longpole enable build"
Enter
Sleep 1s
Type "cmake -S src -B build -G Ninja > /dev/null && cmake --build build"
Enter
Sleep 3s
Type "longpole analyze"
Enter
Sleep 4s

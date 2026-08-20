#!/usr/bin/env nu

let input = "./tailr/benches/data/1M.txt"
let reference = "tail"
let candidate = "target/release/tailr"

^hyperfine -i -L prg $"($reference),($candidate)" $"{{prg}} ($input) > /dev/null"  
^hyperfine -i -L prg $"($reference),($candidate)" $"{{prg}} -n 100000 ($input) > /dev/null"

^hyperfine -i -L prg $"($reference),($candidate)" $"{{prg}} -c 100 ($input) > /dev/null"
^hyperfine -i -L prg $"($reference),($candidate)" $"{{prg}} -c 1000000 ($input) > /dev/null"

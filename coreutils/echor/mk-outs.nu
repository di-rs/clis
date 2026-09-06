#!/usr/bin/env nu

let output = "tests/expected"
mkdir output

def run-echo [args: list<string>, file: string] {
    ^echo ...$args | save --force ($output | path join $file)
}

run-echo ["Hello there"] "hello1.txt"
run-echo ["Hello" "there"] "hello2.txt"
run-echo ["-n" "Hello there"] "hello1.n.txt"
run-echo ["-n" "Hello" "there"] "hello2.n.txt"
#!/usr/bin/env nu

let inputs_dir = "tests/inputs"
let output_dir = "tests/expected"

rm -rf $output_dir
mkdir $output_dir

let bustle = $"($inputs_dir)/the-bustle.txt"
let files = [
    $"($inputs_dir)/empty.txt"
    $"($inputs_dir)/fox.txt"
    $"($inputs_dir)/spiders.txt"
    $bustle
]

for file in $files {
    let name = ($file | path basename)
    ^cat $file | save $"($output_dir)/($name).out"
    ^cat -n $file | save $"($output_dir)/($name).n.out"
    ^cat -b $file | save $"($output_dir)/($name).b.out"
}

^cat ...$files | save $"($output_dir)/all.out"
^cat -n ...$files | save $"($output_dir)/all.n.out"
^cat -b ...$files | save $"($output_dir)/all.b.out"

let bustle_name = $bustle | path basename

open --raw $bustle | ^cat | save $"($output_dir)/($bustle_name).stdin.out"
open --raw $bustle | ^cat -n | save $"($output_dir)/($bustle_name).n.stdin.out"
open --raw $bustle | ^cat -b | save $"($output_dir)/($bustle_name).b.stdin.out"

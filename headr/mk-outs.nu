#!/usr/bin/env nu

let inputs_dir = "tests/inputs"
let output_dir = "tests/expected"

rm -rf $output_dir
mkdir $output_dir

let files = glob $"($inputs_dir)/*.txt"

for file in $files {
    let name = ($file | path basename)

    ^head $file | save $"($output_dir)/($name).out"
    ^head -n 2 $file | save $"($output_dir)/($name).n2.out"
    ^head -n 4 $file | save $"($output_dir)/($name).n4.out"
    ^head -c 1 $file | save $"($output_dir)/($name).c1.out"
    ^head -c 2 $file | save $"($output_dir)/($name).c2.out"
    ^head -c 4 $file | save $"($output_dir)/($name).c4.out"
}

^head ...$files | save $"($output_dir)/all.out"
^head -n 2 ...$files | save $"($output_dir)/all.n2.out"
^head -n 4 ...$files | save $"($output_dir)/all.n4.out"
^head -c 1 ...$files | save $"($output_dir)/all.c1.out"
^head -c 2 ...$files | save $"($output_dir)/all.c2.out"
^head -c 4 ...$files | save $"($output_dir)/all.c4.out"
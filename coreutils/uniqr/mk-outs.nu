#!/usr/bin/env nu

let inputs_dir = "tests/inputs"
let output_dir = "tests/expected"

rm -rf $output_dir
mkdir $output_dir

# Cf https://github.com/coreutils/coreutils/blob/master/tests/misc/uniq.pl
^echo -n "a\na\n"    | save --force $"($inputs_dir)/t1.txt"
^echo -n "a\na"      | save --force $"($inputs_dir)/t2.txt"
^echo -n "a\nb"      | save --force $"($inputs_dir)/t3.txt"
^echo -n "a\na\nb"   | save --force $"($inputs_dir)/t4.txt"
^echo -n "b\na\na\n" | save --force $"($inputs_dir)/t5.txt"
^echo -n "a\nb\nc\n" | save --force $"($inputs_dir)/t6.txt"

let files = glob $"($inputs_dir)/*.txt"

for file in $files {
    let name = ($file | path basename)

    ^uniq      $file | save $"($output_dir)/($name).out"
    ^uniq -c   $file | save $"($output_dir)/($name).c.out"
    open --raw $file | ^uniq | save $"($output_dir)/($name).stdin.out"
    open --raw $file | ^uniq -c | save $"($output_dir)/($name).stdin.c.out"
}
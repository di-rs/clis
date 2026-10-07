#!/usr/bin/env nu

let input_dir = "tests/inputs"
let output_dir = "tests/expected"

rm -rf $output_dir
mkdir $output_dir

let files = [
    $"($input_dir)/twelve.txt",
    $"($input_dir)/empty.txt"
    $"($input_dir)/one.txt"
    $"($input_dir)/three.txt"
    $"($input_dir)/two.txt"
]

for file in $files {
    let basename = $file | path basename
    ^tail        $file | save $"($output_dir)/($basename).out"
    ^tail -n 0   $file | save $"($output_dir)/($basename).n0.out"
    ^tail -n 1   $file | save $"($output_dir)/($basename).n1.out"
    ^tail -n 3   $file | save $"($output_dir)/($basename).n3.out"
    ^tail -n 4   $file | save $"($output_dir)/($basename).n4.out"
    ^tail -n 200 $file | save $"($output_dir)/($basename).n200.out"
    ^tail -c 3   $file | save $"($output_dir)/($basename).c3.out"
    ^tail -c 8   $file | save $"($output_dir)/($basename).c8.out"
    ^tail -c 12  $file | save $"($output_dir)/($basename).c12.out"
    ^tail -c 200 $file | save $"($output_dir)/($basename).c200.out"

    ^tail -n +0  $file | save $"($output_dir)/($basename).n+0.out"
    ^tail -n +1  $file | save $"($output_dir)/($basename).n+1.out"
    ^tail -n +2  $file | save $"($output_dir)/($basename).n+2.out"
    ^tail -c +0  $file | save $"($output_dir)/($basename).c+0.out"
    ^tail -c +1  $file | save $"($output_dir)/($basename).c+1.out"
    ^tail -c +2  $file | save $"($output_dir)/($basename).c+2.out"
}

^tail         ...$files | save $"($output_dir)/all.out"
^tail -n 0    ...$files | save $"($output_dir)/all.n0.out"
^tail -n 1    ...$files | save $"($output_dir)/all.n1.out"
^tail -n 1 -q ...$files | save $"($output_dir)/all.n1.q.out"
^tail -n 3    ...$files | save $"($output_dir)/all.n3.out"
^tail -c 0    ...$files | save $"($output_dir)/all.c0.out"
^tail -c 3    ...$files | save $"($output_dir)/all.c3.out"
^tail -c 8    ...$files | save $"($output_dir)/all.c8.out"
^tail -c 12   ...$files | save $"($output_dir)/all.c12.out"
^tail -n 3 -q ...$files | save $"($output_dir)/all.n3.q.out"

^tail -n +1    ...$files | save $"($output_dir)/all.n+1.out"
^tail -n +3    ...$files | save $"($output_dir)/all.n+3.out"
^tail -c +3    ...$files | save $"($output_dir)/all.c+3.out"
^tail -c +8    ...$files | save $"($output_dir)/all.c+8.out"
^tail -c +12   ...$files | save $"($output_dir)/all.c+12.out"
^tail -n +3 -q ...$files | save $"($output_dir)/all.n+3.q.out"

#!/usr/bin/env nu

let inputs_dir = "tests/inputs"
let output_dir = "tests/expected"

rm -rf $output_dir
mkdir $output_dir

let files = [
    $"($inputs_dir)/empty.txt"
    $"($inputs_dir)/fox.txt"
    $"($inputs_dir)/atlamal.txt"
]

for file in $files {
    let name = ($file | path basename)
    ^wc      $file | save $"($output_dir)/($name).out"
    ^wc -l   $file | save $"($output_dir)/($name).l.out"
    ^wc -w   $file | save $"($output_dir)/($name).w.out"
    ^wc -c   $file | save $"($output_dir)/($name).c.out"
    ^wc -m   $file | save $"($output_dir)/($name).m.out"
    ^wc -lwm $file | save $"($output_dir)/($name).lwm.out"
    ^wc -wc  $file | save $"($output_dir)/($name).wc.out"
    ^wc -wm  $file | save $"($output_dir)/($name).wm.out"
    ^wc -wl  $file | save $"($output_dir)/($name).wl.out"
    ^wc -cl  $file | save $"($output_dir)/($name).cl.out"
    ^wc -ml  $file | save $"($output_dir)/($name).ml.out"
}

open --raw $"($inputs_dir)/atlamal.txt" | wc | save $"($output_dir)/atlamal.txt.stdin.out"

wc      ...$files | save $"($output_dir)/all.out"
wc -l   ...$files | save $"($output_dir)/all.l.out"
wc -w   ...$files | save $"($output_dir)/all.w.out"
wc -c   ...$files | save $"($output_dir)/all.c.out"
wc -m   ...$files | save $"($output_dir)/all.m.out"
wc -lwm ...$files | save $"($output_dir)/all.lwm.out"
wc -wc  ...$files | save $"($output_dir)/all.wc.out"
wc -wm  ...$files | save $"($output_dir)/all.wm.out"
wc -wl  ...$files | save $"($output_dir)/all.wl.out"
wc -cl  ...$files | save $"($output_dir)/all.cl.out"
wc -ml  ...$files | save $"($output_dir)/all.ml.out"

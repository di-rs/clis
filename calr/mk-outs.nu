#!/usr/bin/env nu
# 
let output_dir = "tests/expected"

rm -rf $output_dir
mkdir $output_dir

^cal 2020 | save $"($output_dir)/2020.txt"
^cal 2 2020 | save $"($output_dir)/2-2020.txt"
^cal 4 2020 | save $"($output_dir)/4-2020.txt"
^cal 5 2020 | save $"($output_dir)/5-2020.txt"

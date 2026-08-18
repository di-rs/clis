#!/usr/bin/env nu

let input_dir = "tests/inputs"
let output_dir = "tests/expected"

rm -rf $output_dir
mkdir $output_dir

let empty = $"($input_dir)/empty.txt"
let blank = $"($input_dir)/blank.txt"
let file1 = $"($input_dir)/file1.txt"
let file2 = $"($input_dir)/file2.txt"

^comm         $empty $empty | save $"($output_dir)/empty_empty.out"
^comm         $file1 $empty | save $"($output_dir)/file1_empty.out"
^comm         $empty $file2 | save $"($output_dir)/empty_file2.out"
^comm         $file1 $file1 | save $"($output_dir)/file1_file1.out"

^comm         $blank $file1 | save $"($output_dir)/blank_file1.out"
^comm         $file1 $blank | save $"($output_dir)/file1_blank.out"
^comm -1      $file1 $blank | save $"($output_dir)/file1_blank.1.out"
^comm -2      $file1 $blank | save $"($output_dir)/file1_blank.2.out"
^comm -3      $file1 $blank | save $"($output_dir)/file1_blank.3.out"

^comm         $file1 $file2 | save $"($output_dir)/file1_file2.out"
^comm -1      $file1 $file2 | save $"($output_dir)/file1_file2.1.out"
^comm -2      $file1 $file2 | save $"($output_dir)/file1_file2.2.out"
^comm -3      $file1 $file2 | save $"($output_dir)/file1_file2.3.out"

^comm -12     $file1 $file2 | save $"($output_dir)/file1_file2.12.out"
^comm -23     $file1 $file2 | save $"($output_dir)/file1_file2.23.out"
^comm -13     $file1 $file2 | save $"($output_dir)/file1_file2.13.out"
^comm -123    $file1 $file2 | save $"($output_dir)/file1_file2.123.out"

^comm -i -1   $file1 $file2 | save $"($output_dir)/file1_file2.1.i.out"
^comm -i -2   $file1 $file2 | save $"($output_dir)/file1_file2.2.i.out"
^comm -i -3   $file1 $file2 | save $"($output_dir)/file1_file2.3.i.out"

^comm -i -12  $file1 $file2 | save $"($output_dir)/file1_file2.12.i.out"
^comm -i -23  $file1 $file2 | save $"($output_dir)/file1_file2.23.i.out"
^comm -i -13  $file1 $file2 | save $"($output_dir)/file1_file2.13.i.out"
^comm -i -123 $file1 $file2 | save $"($output_dir)/file1_file2.123.i.out"

^comm         $file1 $file2 | ^sed "s/\t/:/g" | save $"($output_dir)/file1_file2.delim.out"
^comm -1      $file1 $file2 | ^sed "s/\t/:/g" | save $"($output_dir)/file1_file2.1.delim.out"
^comm -2      $file1 $file2 | ^sed "s/\t/:/g" | save $"($output_dir)/file1_file2.2.delim.out"
^comm -3      $file1 $file2 | ^sed "s/\t/:/g" | save $"($output_dir)/file1_file2.3.delim.out"
^comm -12     $file1 $file2 | ^sed "s/\t/:/g" | save $"($output_dir)/file1_file2.12.delim.out"
^comm -23     $file1 $file2 | ^sed "s/\t/:/g" | save $"($output_dir)/file1_file2.23.delim.out"
^comm -13     $file1 $file2 | ^sed "s/\t/:/g" | save $"($output_dir)/file1_file2.13.delim.out"
^comm -123    $file1 $file2 | ^sed "s/\t/:/g" | save $"($output_dir)/file1_file2.123.delim.out"

#!/usr/bin/env nu

let csv_path = "tests/inputs/movies1.csv"
let tsv_path = "tests/inputs/movies1.tsv"
let books_tsv = "tests/inputs/books.tsv"
let output_dir = "tests/expected"

rm -rf $output_dir
mkdir $output_dir

let csv_name = $csv_path | path basename;
let tsv_name = $tsv_path | path basename;

let fields = [
    "1", "2", "3", "1-2", "2-3", "1-3"
]

for field in $fields {
    ^cut -f $field      $tsv_path | save $"($output_dir)/($tsv_name).f($field).out"
    ^cut -f $field -d , $csv_path | save $"($output_dir)/($csv_name).f($field).dcomma.out"
}

let bytes_positions = [
    "1", "2", "8", "1-2", "2-3", "1-8"
]

for position in $bytes_positions {
    ^cut -b $position $tsv_path | save $"($output_dir)/($tsv_name).b($position).out"
    ^cut -b $position $csv_path | save $"($output_dir)/($csv_name).b($position).out"
}

let chars_positions = [
    "1", "2", "8", "1-2", "2-3", "1-8"
]

for position in $chars_positions {
    ^cut -c $position $tsv_path | save $"($output_dir)/($tsv_name).c($position).out"
    ^cut -c $position $csv_path | save $"($output_dir)/($csv_name).c($position).out"
}

^echo -e "AA\nÉÉ\nSS\nJJ" | save $"($output_dir)/books.c1,1.out"

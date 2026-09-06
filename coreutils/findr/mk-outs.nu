#!/usr/bin/env nu

let inputs_dir = "tests/inputs"
let output_dir = "tests/expected"

mkdir $output_dir

^find $"($inputs_dir)" | save $"($output_dir)/path1.txt"
^find $"($inputs_dir)/a" | save $"($output_dir)/path_a.txt"
^find $"($inputs_dir)/a/b" | save $"($output_dir)/path_a_b.txt"
^find $"($inputs_dir)/d" | save $"($output_dir)/path_d.txt"
^find $"($inputs_dir)/a/b" $"($inputs_dir)/d" | save $"($output_dir)/path_a_b_d.txt"

^find $"($inputs_dir)" -type f | save $"($output_dir)/type_f.txt"
^find $"($inputs_dir)/a" -type f | save $"($output_dir)/type_f_path_a.txt"
^find $"($inputs_dir)/a/b" -type f | save $"($output_dir)/type_f_path_a_b.txt"
^find $"($inputs_dir)/d" -type f | save $"($output_dir)/type_f_path_d.txt"
^find $"($inputs_dir)/a/b" $"($inputs_dir)/d" -type f | save $"($output_dir)/type_f_path_a_b_d.txt"

^find $"($inputs_dir)" -type d | save $"($output_dir)/type_d.txt"
^find $"($inputs_dir)/a" -type d | save $"($output_dir)/type_d_path_a.txt"
^find $"($inputs_dir)/a/b" -type d | save $"($output_dir)/type_d_path_a_b.txt"
^find $"($inputs_dir)/d" -type d | save $"($output_dir)/type_d_path_d.txt"
^find $"($inputs_dir)/a/b" $"($inputs_dir)/d" -type d | save $"($output_dir)/type_d_path_a_b_d.txt"

^find $"($inputs_dir)" -type l | save $"($output_dir)/type_l.txt"
^find $"($inputs_dir)" -type f -o -type l | save $"($output_dir)/type_f_l.txt"

^find $"($inputs_dir)" -name \*.csv | save $"($output_dir)/name_csv.txt"
^find $"($inputs_dir)" -name \*.csv -o -name \*.mp3 | save $"($output_dir)/name_csv_mp3.txt"
^find $"($inputs_dir)/a" $"($inputs_dir)/d" -name \*.txt | save $"($output_dir)/name_txt_path_a_d.txt"

^find $"($inputs_dir)" -name a* | save $"($output_dir)/name_a.txt"
^find $"($inputs_dir)" -type f -name a* | save $"($output_dir)/type_f_name_a.txt"
^find $"($inputs_dir)" -type d -name a* | save $"($output_dir)/type_d_name_a.txt"

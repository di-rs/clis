#!/usr/bin/env nu
# Testing procedure: ../../.agents/skills/cli-testing/SKILL.md

let inputs_dir = "tests/inputs"
let output_dir = "tests/expected"

mkdir $output_dir

^find $"($inputs_dir)" | save --force $"($output_dir)/path1.txt"
^find $"($inputs_dir)/a" | save --force $"($output_dir)/path_a.txt"
^find $"($inputs_dir)/a/b" | save --force $"($output_dir)/path_a_b.txt"
^find $"($inputs_dir)/d" | save --force $"($output_dir)/path_d.txt"
^find $"($inputs_dir)/a/b" $"($inputs_dir)/d" | save --force $"($output_dir)/path_a_b_d.txt"

^find $"($inputs_dir)" -type f | save --force $"($output_dir)/type_f.txt"
^find $"($inputs_dir)/a" -type f | save --force $"($output_dir)/type_f_path_a.txt"
^find $"($inputs_dir)/a/b" -type f | save --force $"($output_dir)/type_f_path_a_b.txt"
^find $"($inputs_dir)/d" -type f | save --force $"($output_dir)/type_f_path_d.txt"
^find $"($inputs_dir)/a/b" $"($inputs_dir)/d" -type f | save --force $"($output_dir)/type_f_path_a_b_d.txt"

^find $"($inputs_dir)" -type d | save --force $"($output_dir)/type_d.txt"
^find $"($inputs_dir)/a" -type d | save --force $"($output_dir)/type_d_path_a.txt"
^find $"($inputs_dir)/a/b" -type d | save --force $"($output_dir)/type_d_path_a_b.txt"
^find $"($inputs_dir)/d" -type d | save --force $"($output_dir)/type_d_path_d.txt"
^find $"($inputs_dir)/a/b" $"($inputs_dir)/d" -type d | save --force $"($output_dir)/type_d_path_a_b_d.txt"

^find $"($inputs_dir)" -type l | save --force $"($output_dir)/type_l.txt"
^find $"($inputs_dir)" -type f -o -type l | save --force $"($output_dir)/type_f_l.txt"

^find $"($inputs_dir)" -name "*.csv" | save --force $"($output_dir)/name_csv.txt"
^find $"($inputs_dir)" -name "*.csv" -o -name "*.mp3" | save --force $"($output_dir)/name_csv_mp3.txt"
^find $"($inputs_dir)/a" $"($inputs_dir)/d" -name "*.txt" | save --force $"($output_dir)/name_txt_path_a_d.txt"

^find $"($inputs_dir)" -name "a*" | save --force $"($output_dir)/name_a.txt"
^find $"($inputs_dir)" -type f -name "a*" | save --force $"($output_dir)/type_f_name_a.txt"
^find $"($inputs_dir)" -type d -name "a*" | save --force $"($output_dir)/type_d_name_a.txt"

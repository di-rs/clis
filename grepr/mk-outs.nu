#!/usr/bin/env nu

let input_dir = "tests/inputs"
let output_dir = "tests/expected"

rm -rf $output_dir
mkdir $output_dir

let files = glob $"($input_dir)/*.txt"

def --wrapped grep [...args] {
    ^grep ...$args | complete | get stdout
}

# Empty file
grep foo $"($input_dir)/empty.txt" | save $"($output_dir)/foo.empty.txt"

# Empty regex
grep "" $"($input_dir)/fox.txt" | save $"($output_dir)/empty_regex.fox.txt"

# Case-sensitive
grep The $"($input_dir)/bustle.txt" | save $"($output_dir)/bustle.txt.the.capitalized"
grep the $"($input_dir)/bustle.txt" | save $"($output_dir)/bustle.txt.the.lowercase"
grep -i the $"($input_dir)/bustle.txt" | save $"($output_dir)/bustle.txt.the.lowercase.insensitive"
grep nobody $"($input_dir)/nobody.txt" | save $"($output_dir)/nobody.txt"
grep -i nobody $"($input_dir)/nobody.txt" | save $"($output_dir)/nobody.txt.insensitive"

# Case-sensitive, multiple files
grep The ...$files | save $"($output_dir)/all.the.capitalized"
grep -i the ...$files | save $"($output_dir)/all.the.lowercase.insensitive"

# Recursive, handle directory
grep -r dog $input_dir | save $"($output_dir)/dog.recursive"

# Recursive, insensitive
grep -ri then $input_dir | save $"($output_dir)/the.recursive.insensitive"

# Case-sensitive, count
grep -c The $"($input_dir)/bustle.txt" | save $"($output_dir)/bustle.txt.the.capitalized.count"
grep -c the $"($input_dir)/bustle.txt" | save $"($output_dir)/bustle.txt.the.lowercase.count"
grep -ci the $"($input_dir)/bustle.txt" | save $"($output_dir)/bustle.txt.the.lowercase.insensitive.count"
grep -c nobody $"($input_dir)/nobody.txt" | save $"($output_dir)/nobody.txt.count"
grep -ci nobody $"($input_dir)/nobody.txt" | save $"($output_dir)/nobody.txt.insensitive.count"

# Case-sensitive, count, multiple files
grep -c The ...$files | save $"($output_dir)/all.the.capitalized.count"
grep -ci the ...$files | save $"($output_dir)/all.the.lowercase.insensitive.count"

# Recursive, insensitive, count
grep -cri the $input_dir | save $"($output_dir)/the.recursive.insensitive.count"

# STDIN, insensitive, count
^cat ...$files | grep -ci the - | save $"($output_dir)/the.recursive.insensitive.count.stdin"

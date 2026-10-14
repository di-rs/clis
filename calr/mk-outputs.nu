#!/usr/bin/env nu
# Testing procedure: ../.agents/skills/cli-testing/SKILL.md
#
let output_dir = "tests/expected"

# Keep the manually authored proleptic-Gregorian edge-case fixtures.
mkdir $output_dir

^cal 2020 | save --force $"($output_dir)/2020.txt"
^cal 2 2020 | save --force $"($output_dir)/2-2020.txt"
^cal 4 2020 | save --force $"($output_dir)/4-2020.txt"
^cal 5 2020 | save --force $"($output_dir)/5-2020.txt"

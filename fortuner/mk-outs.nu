#!/usr/bin/env nu

let input_dir = "./tests/inputs"
let output_dir = "./tests/expected"

rm -rf $output_dir
mkdir $output_dir

let files = [
    $"($input_dir)/literature",
    $"($input_dir)/quotes"
]

def --wrapped fortune [...args] {
    ^grep ...$args | complete | get stdout
}

fortune -m 'Yogi Berra' ...$files out> $"($output_dir)/berra_cap.out" err> $"($output_dir)/berra_cap.err"
fortune -m 'Mark Twain' ...$files out> $"($output_dir)/twain_cap.out" err> $"($output_dir)/twain_cap.err"

fortune -m 'yogi berra' ...$files out> $"($output_dir)/berra_lower.out" err> $"($output_dir)/berra_lower.err"
fortune -m 'mark twain' ...$files out> $"($output_dir)/twain_lower.out" err> $"($output_dir)/twain_lower.err"

fortune -i -m 'yogi berra' ...$files out> $"($output_dir)/berra_lower_i.out" err> $"($output_dir)/berra_lower_i.err"
fortune -i -m 'mark twain' ...$files out> $"($output_dir)/twain_lower_i.out" err> $"($output_dir)/twain_lower_i.err"

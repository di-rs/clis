#!/usr/bin/env nu


def main [dir?: path] {
    let dir = $dir | default $env.PWD

    ^chmod 755 $"($dir)/tests/inputs/dir"
    ^chmod 600 $"($dir)/tests/inputs/fox.txt"
    ^chmod 644 $"($dir)/tests/inputs/.hidden"  $"($dir)/tests/inputs/empty.txt"  $"($dir)/tests/inputs/bustle.txt"  $"($dir)/tests/inputs/dir/.gitkeep" $"($dir)/tests/inputs/dir/spiders.txt"

    print $"Done, fixed files in \"($dir)\"."
}

#!/usr/bin/env nu

let input_dir = "./tests/inputs"

rm -f $"($input_dir)/*.dat"

glob $"($input_dir)/*" | where {|file| ($file | path type) == "file" } | each {|file| 
    print ($file | path basename)
    ^strfile -c "%" $file $"($file).dat" | ignore
}

print "Done."
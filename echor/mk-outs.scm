(require-builtin "steel/process")
(require "steel/result")

(define output-dir "tests/expected")
(create-directory! output-dir)

(define (run-echo inputs output-file)
    (define file-path (string-append output-dir "/" output-file))
    (if (path-exists? file-path) (delete-file! file-path))
    (define output (open-output-file file-path))

    (~> (command "echo" inputs)
        (with-stdout output)
        spawn-process
        unwrap-ok
        wait
    )
)
        
(run-echo (list "Hello there") "hello1.txt")
(run-echo (list "Hello" "there") "hello2.txt")
(run-echo (list "-n" "Hello there") "hello1.n.txt")
(run-echo (list "-n" "Hello" "there") "hello2.n.txt")
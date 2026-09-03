test:
    cargo nextest run

lint:
    cargo clippy --all-targets 

watch:
   watchexec -c -e rs "just lint && just test"
use super::bench_api as cli_bench;
use cli_bench::*;
// Tiny original literal fault fixtures exercise plumbing only, never benchmark generation.
pub fn dataset_bindings(
    root: &std::path::Path,
    store: &Store,
    script: &str,
) -> Result<cli_bench::RoleBindings, Box<dyn std::error::Error>> {
    use std::os::unix::fs::PermissionsExt;
    let program = root.join("literal-fixture-generator");
    std::fs::write(&program, format!("#!/bin/sh\n{script}\n"))?;
    std::fs::set_permissions(&program, std::fs::Permissions::from_mode(0o700))?;
    let artifact = register_binary(&program, None, store)?;
    std::fs::create_dir_all(root.join("home"))?;
    std::fs::create_dir_all(root.join("config"))?;
    Ok(cli_bench::RoleBindings {
        roles: std::collections::BTreeMap::new(),
        generator: Some(cli_bench::BoundExecutable {
            path: store.artifact_path(&artifact),
            artifact,
        }),
        environment: std::collections::BTreeMap::new(),
        home: root.join("home"),
        config: root.join("config"),
        pipeline: None,
    })
}
pub fn dataset_runner() -> cli_bench::ProcessRunner {
    cli_bench::ProcessRunner::new(cli_bench::ExecutionPolicy {
        timeout: std::time::Duration::from_millis(250),
        max_stream_bytes: 1024,
        cancellation: std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false)),
    })
}
pub const LITERAL_GENERATOR: &str = r#"
[ "$1" = text ] || exit 8
[ "$8" = --seed ] || exit 9
[ "$LC_ALL" = C ] || exit 10
for output do :; done
printf 'fixture diagnostic\n' >&2
if [ -f "$HOME/drift" ]; then printf 'WXYZ\nIJKL\n' > "$output"; else printf 'ABCD\nEFGH\n' > "$output"; fi
"#;

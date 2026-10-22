use std::path::{Path, PathBuf};

use glob::MatchOptions;

pub fn args(input: &str) -> Vec<String> {
    let args = shlex::split(input).unwrap_or_default();
    let mut parsed_args = Vec::new();

    for arg in args {
        let arg = replace_tilde_with_home(&arg);
        if arg.contains('*') {
            match expand_glob(&arg) {
                Ok(glob_args) => parsed_args.extend(glob_args),
                Err(_) => parsed_args.push(arg),
            }
        } else {
            parsed_args.push(arg);
        }
    }

    parsed_args
}

fn replace_tilde_with_home<T>(path: T) -> String
where
    T: AsRef<str>,
{
    let home_env = std::env::var("HOME");
    let path = path.as_ref();
    match (path.strip_prefix("~"), home_env) {
        (Some(rest_path), Ok(home)) => {
            format!("{home}{rest_path}")
        }
        _ => path.to_owned(),
    }
}

pub fn replace_home_with_tilde<T>(path: T) -> PathBuf
where
    T: AsRef<Path>,
{
    let path = path.as_ref();
    let Ok(home) = std::env::var("HOME") else {
        return path.to_owned();
    };
    path.strip_prefix(home).map_or_else(
        |_| path.to_owned(),
        |rest_path| PathBuf::from("~").join(rest_path),
    )
}

fn expand_glob<T: AsRef<str>>(path: T) -> anyhow::Result<Vec<String>> {
    let paths = glob::glob_with(
        path.as_ref(),
        MatchOptions {
            case_sensitive: false,
            require_literal_leading_dot: false,
            require_literal_separator: false,
        },
    )?;

    Ok(paths
        .flatten()
        .map(|p| p.display().to_string())
        .collect::<Vec<_>>())
}

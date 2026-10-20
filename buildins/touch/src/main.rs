use color_eyre::{Result, eyre::Context};
use rustix::fs::Timestamps;
use touch::{Options, reference_times};

mod cli;
use cli::{Cli, date_times, legacy_timestamp};

fn main() -> std::process::ExitCode {
    cli_log::run("touch", |cli| match run(cli) {
        Ok(status) => status,
        Err(error) => {
            cli_log::diagnostic!("touch: {error:#}");
            1
        }
    })
}

fn run(mut cli: Cli) -> Result<i32> {
    let mut times = cli
        .reference
        .as_deref()
        .map(|path| {
            reference_times(path, cli.no_dereference)
                .wrap_err_with(|| format!("failed to read timestamps for {}", path.display()))
        })
        .transpose()?;
    if let Some(date) = &cli.date {
        times = Some(date_times(date, times.as_ref())?);
    }
    if let Some(time) = cli.timestamp {
        times = Some(Timestamps {
            last_access: time,
            last_modification: time,
        });
    }
    if times.is_none()
        && cli.files.len() >= 2
        && let Some(first) = cli.files.first()
        && let Some(time) = legacy_timestamp(first)?
    {
        if cli.adjustment.unwrap_or(0) == 0 {
            times = Some(Timestamps {
                last_access: time,
                last_modification: time,
            });
        }
        cli.files.remove(0);
    }
    let access = cli.access || matches!(cli.time.as_deref(), Some("access" | "atime" | "use"));
    let modify = cli.modify || matches!(cli.time.as_deref(), Some("modify" | "mtime"));
    let options = Options {
        access: access || !modify,
        modify: modify || !access,
        no_create: cli.no_create || cli.no_dereference || cli.adjustment.is_some(),
        no_dereference: cli.no_dereference,
        adjustment: cli.adjustment.unwrap_or(0),
        times,
    };
    let mut status = 0;
    for file in &cli.files {
        if let Err(error) = options.apply(file) {
            cli_log::diagnostic!("touch: {error:#}");
            status = 1;
        }
    }
    Ok(status)
}

#![allow(clippy::unwrap_used, clippy::indexing_slicing)]

use clap::{Arg, ArgAction, Command};

#[test]
fn describes_nested_public_arguments_and_their_owners() {
    let command = Command::new("fixture")
        .disable_help_subcommand(true)
        .arg(
            Arg::new("quiet")
                .short('q')
                .long("quiet")
                .global(true)
                .action(ArgAction::Count),
        )
        .arg(Arg::new("secret").long("secret").hide(true))
        .subcommand(
            Command::new("run")
                .visible_alias("r")
                .arg(Arg::new("file").value_name("FILE").required(true))
                .arg(
                    Arg::new("format")
                        .long("format")
                        .visible_alias("fmt")
                        .value_parser(["text", "json"])
                        .default_value("text")
                        .help("Output format"),
                ),
        )
        .subcommand(Command::new("internal").hide(true));
    let value = cli_schema::schema(command);
    let root = &value["command"];
    let children = root["subcommands"].as_array().unwrap();
    assert_eq!(children.len(), 1);
    let child = &children[0];
    assert_eq!(child["aliases"], serde_json::json!(["r"]));
    let args = child["args"].as_array().unwrap();
    let file = args.iter().find(|arg| arg["id"] == "file").unwrap();
    assert_eq!(file["required"], true);
    assert_eq!(file["index"], 1);
    assert_eq!(file["value_names"], serde_json::json!(["FILE"]));
    let format = args.iter().find(|arg| arg["id"] == "format").unwrap();
    assert_eq!(
        format["possible_values"],
        serde_json::json!(["text", "json"])
    );
    assert_eq!(format["default_values"], serde_json::json!(["text"]));
    assert_eq!(format["aliases"], serde_json::json!(["--fmt"]));
    assert_eq!(format["num_args"], serde_json::json!({"min":1,"max":1}));
    let global = args.iter().find(|arg| arg["id"] == "quiet").unwrap();
    assert_eq!(global["owner"], serde_json::json!([]));
    assert_eq!(global["global"], true);
    assert!(
        !root["args"]
            .as_array()
            .unwrap()
            .iter()
            .any(|arg| arg["id"] == "secret")
    );
}

#[test]
fn respects_hidden_values_and_unbounded_arity() {
    let value = cli_schema::schema(
        Command::new("fixture")
            .arg(
                Arg::new("token")
                    .long("token")
                    .default_value("private")
                    .hide_default_value(true),
            )
            .arg(Arg::new("files").num_args(1..)),
    );
    let args = value["command"]["args"].as_array().unwrap();
    assert_eq!(
        args.iter().find(|arg| arg["id"] == "token").unwrap()["default_values"],
        serde_json::json!([])
    );
    assert_eq!(
        args.iter().find(|arg| arg["id"] == "files").unwrap()["num_args"],
        serde_json::json!({"min":1,"max":null})
    );
    assert!(!value.to_string().contains("private"));
}

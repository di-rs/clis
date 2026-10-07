#![no_main]
use libfuzzer_sys::fuzz_target;
use std::io::{BufReader, Cursor};
use tailr::{TakeValue, print_bytes, print_stream_bytes};

fuzz_target!(|data: &[u8]| {
    let (selector, bytes) = data.split_first().unwrap_or((&0, &[]));
    let count = match selector % 6 {
        0 => TakeValue::PlusZero,
        1 => TakeValue::TakeNum(i64::MIN),
        2 => TakeValue::TakeNum(i64::MAX),
        3 => TakeValue::TakeNum(0),
        4 => TakeValue::TakeNum(-i64::from(*selector)),
        _ => TakeValue::TakeNum(i64::from(*selector)),
    };
    let mut stream = Vec::new();
    print_stream_bytes(BufReader::with_capacity(3, bytes), &mut stream, count).unwrap();
    let mut seek = Vec::new();
    print_bytes(
        Cursor::new(bytes),
        &mut seek,
        count,
        u64::try_from(bytes.len()).unwrap(),
    )
    .unwrap();
    assert_eq!(stream, seek);
});

use std::io::{self, BufReader, Cursor, Read, Seek, SeekFrom, Write};
use tailr::{TakeValue, print_bytes, print_stream_bytes};

#[test]
fn stream_and_seek_bytes_agree() {
    let input = b"\0\xffA\r\nz";
    for (count, expected) in [
        (TakeValue::PlusZero, input.as_slice()),
        (TakeValue::TakeNum(i64::MIN), input.as_slice()),
        (TakeValue::TakeNum(-99), input.as_slice()),
        (TakeValue::TakeNum(-3), b"\r\nz".as_slice()),
        (TakeValue::TakeNum(-1), b"z".as_slice()),
        (TakeValue::TakeNum(0), b"".as_slice()),
        (TakeValue::TakeNum(1), input.as_slice()),
        (TakeValue::TakeNum(3), b"A\r\nz".as_slice()),
        (TakeValue::TakeNum(i64::MAX), b"".as_slice()),
    ] {
        for (bytes, wanted) in [
            (input.as_slice(), expected),
            (b"".as_slice(), b"".as_slice()),
        ] {
            let mut stream = Vec::new();
            print_stream_bytes(BufReader::with_capacity(2, bytes), &mut stream, count).unwrap();
            let mut seek = Vec::new();
            print_bytes(
                Cursor::new(bytes),
                &mut seek,
                count,
                u64::try_from(bytes.len()).unwrap(),
            )
            .unwrap();
            assert_eq!(stream, wanted, "stream {count:?}");
            assert_eq!(seek, wanted, "seek {count:?}");
        }
    }
}

struct BrokenReader;
impl Read for BrokenReader {
    fn read(&mut self, _: &mut [u8]) -> io::Result<usize> {
        Err(io::ErrorKind::PermissionDenied.into())
    }
}
impl Seek for BrokenReader {
    fn seek(&mut self, _: SeekFrom) -> io::Result<u64> {
        Ok(0)
    }
}

#[test]
fn read_errors_reach_the_caller() {
    let stream = print_stream_bytes(
        BufReader::new(BrokenReader),
        Vec::new(),
        TakeValue::PlusZero,
    );
    let seek = print_bytes(BrokenReader, Vec::new(), TakeValue::PlusZero, 1);
    for result in [stream, seek] {
        assert_eq!(result.unwrap_err().kind(), io::ErrorKind::PermissionDenied);
    }
}

#[derive(Default)]
struct ShortWriter {
    bytes: Vec<u8>,
    fail_after: Option<usize>,
    flushes: usize,
}
impl Write for ShortWriter {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        if self.fail_after == Some(self.bytes.len()) {
            return Err(io::ErrorKind::BrokenPipe.into());
        }
        if let Some(byte) = bytes.first() {
            self.bytes.push(*byte);
            Ok(1)
        } else {
            Ok(0)
        }
    }
    fn flush(&mut self) -> io::Result<()> {
        self.flushes = self.flushes.saturating_add(1);
        Err(io::ErrorKind::BrokenPipe.into())
    }
}

#[test]
fn partial_writes_are_completed_and_flushing_belongs_to_the_caller() {
    let mut writer = ShortWriter::default();
    print_stream_bytes(Cursor::new(b"abc"), &mut writer, TakeValue::PlusZero).unwrap();
    assert_eq!(writer.bytes, b"abc");
    assert_eq!(writer.flushes, 0);
    assert_eq!(
        writer.flush().unwrap_err().kind(),
        io::ErrorKind::BrokenPipe
    );
}

#[test]
fn write_failure_preserves_the_accepted_prefix() {
    for stream in [true, false] {
        let mut writer = ShortWriter {
            fail_after: Some(1),
            ..ShortWriter::default()
        };
        let result = if stream {
            print_stream_bytes(Cursor::new(b"abc"), &mut writer, TakeValue::PlusZero)
        } else {
            print_bytes(Cursor::new(b"abc"), &mut writer, TakeValue::PlusZero, 3)
        };
        assert_eq!(result.unwrap_err().kind(), io::ErrorKind::BrokenPipe);
        assert_eq!(writer.bytes, b"a");
        assert_eq!(writer.flushes, 0);
    }
}

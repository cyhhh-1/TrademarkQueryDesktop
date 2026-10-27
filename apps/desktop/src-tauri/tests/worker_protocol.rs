use serde_json::json;
use trademark_query_desktop_lib::files::{template_contents, validate_save_target};
use trademark_query_desktop_lib::worker::LineDecoder;

#[test]
fn splits_multiple_worker_events_without_losing_utf8() {
    let mut decoder = LineDecoder::default();
    let events = decoder
        .push("{\"type\":\"hello\"}\n{\"message\":\"已暂停\"}\n".as_bytes())
        .unwrap();

    assert_eq!(events.len(), 2);
    assert_eq!(events[0], json!({"type": "hello"}));
    assert_eq!(events[1]["message"], "已暂停");
}

#[test]
fn retains_incomplete_utf8_line_until_next_chunk() {
    let payload = "{\"message\":\"已暂停\"}\n".as_bytes();
    let split = payload.len() - 4;
    let mut decoder = LineDecoder::default();

    assert!(decoder.push(&payload[..split]).unwrap().is_empty());
    assert_eq!(
        decoder.push(&payload[split..]).unwrap()[0]["message"],
        "已暂停"
    );
}

#[test]
fn refuses_to_save_over_original_input() {
    let input = std::path::PathBuf::from(r"C:\work\商标申请.xlsx");

    assert!(validate_save_target(&input, &input).is_err());
}

#[test]
fn accepts_different_xlsx_target() {
    let input = std::path::PathBuf::from(r"C:\work\商标申请.xlsx");
    let target = std::path::PathBuf::from(r"C:\work\商标申请_已查询.xlsx");

    assert!(validate_save_target(&input, &target).is_ok());
}

#[test]
fn embeds_both_valid_xlsx_templates() {
    for kind in ["application", "uniqueness"] {
        let (bytes, filename) = template_contents(kind).unwrap();
        assert!(filename.ends_with(".xlsx"));
        assert!(bytes.starts_with(b"PK"), "xlsx must be a ZIP container");
        assert!(bytes.len() > 10_000);
    }
    assert!(template_contents("unknown").is_err());
}

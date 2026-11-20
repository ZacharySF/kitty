//! DOCX paragraph text: bounded archive reads and namespace-checked WordprocessingML only.

use std::io::{Read, Seek};

use quick_xml::events::Event;
use quick_xml::name::ResolveResult;
use quick_xml::NsReader;

const MAIN: &[u8] = b"http://schemas.openxmlformats.org/wordprocessingml/2006/main";
const MAX_ENTRIES: usize = 1024;
const MAX_ARCHIVE: u64 = 16 * 1024 * 1024;
const MAX_DOCUMENT: u64 = 4 * 1024 * 1024;
const MAX_TEXT: usize = 1024 * 1024;

fn entity(name: &str) -> Option<char> {
    Some(match name {
        "amp" => '&',
        "lt" => '<',
        "gt" => '>',
        "quot" => '"',
        "apos" => '\'',
        _ => return None,
    })
}

/// Paragraphs of `word/document.xml`, separated by blank lines.
pub fn text<R: Read + Seek>(stream: R) -> Result<String, String> {
    let mut archive = zip::ZipArchive::new(stream).map_err(|_| "DOCX is not a readable archive".to_string())?;
    if archive.len() > MAX_ENTRIES {
        return Err("DOCX archive exceeds extraction limits".into());
    }
    let mut total = 0u64;
    for index in 0..archive.len() {
        total += archive.by_index_raw(index).map_err(|_| "DOCX archive is damaged".to_string())?.size();
    }
    if total > MAX_ARCHIVE {
        return Err("DOCX archive exceeds extraction limits".into());
    }
    let entry = archive.by_name("word/document.xml").map_err(|_| "DOCX has no document text".to_string())?;
    if entry.size() > MAX_DOCUMENT {
        return Err("DOCX text exceeds extraction limits".into());
    }
    let mut raw = Vec::new();
    // Declared sizes can lie; bound what is actually inflated.
    entry.take(MAX_DOCUMENT + 1).read_to_end(&mut raw).map_err(|_| "DOCX archive is damaged".to_string())?;
    if raw.len() as u64 > MAX_DOCUMENT {
        return Err("DOCX text exceeds extraction limits".into());
    }
    let upper = raw.to_ascii_uppercase();
    if upper.windows(9).any(|w| w == b"<!DOCTYPE") || upper.windows(8).any(|w| w == b"<!ENTITY") {
        return Err("DOCX XML entities are not supported".into());
    }
    let mut reader = NsReader::from_reader(raw.as_slice());
    let mut buffer = Vec::new();
    let mut open: Vec<String> = Vec::new();
    let mut paragraphs: Vec<String> = Vec::new();
    let mut in_text = false;
    let invalid = |_| "DOCX XML is malformed".to_string();
    loop {
        let (namespace, event) = reader.read_resolved_event_into(&mut buffer).map_err(invalid)?;
        let main = matches!(namespace, ResolveResult::Bound(ns) if ns.as_ref() == MAIN);
        match event {
            Event::Start(element) if main => match element.local_name().as_ref() {
                b"p" => open.push(String::new()),
                b"t" => in_text = true,
                _ => {}
            },
            Event::Empty(element) if main && element.local_name().as_ref() == b"p" => paragraphs.push(String::new()),
            Event::End(element) if main => match element.local_name().as_ref() {
                b"p" => paragraphs.extend(open.pop()),
                b"t" => in_text = false,
                _ => {}
            },
            Event::Text(value) if in_text => {
                let decoded = value.decode().map_err(|_| "DOCX XML is malformed".to_string())?;
                if let Some(current) = open.last_mut() {
                    current.push_str(&decoded);
                }
            }
            Event::GeneralRef(reference) if in_text => {
                let character = match reference.resolve_char_ref().map_err(|_| "DOCX XML is malformed".to_string())? {
                    Some(character) => character,
                    None => {
                        let name = reference.decode().map_err(|_| "DOCX XML is malformed".to_string())?;
                        entity(&name).ok_or_else(|| "DOCX XML entities are not supported".to_string())?
                    }
                };
                if let Some(current) = open.last_mut() {
                    current.push(character);
                }
            }
            Event::Eof => break,
            _ => {}
        }
        if paragraphs.iter().map(String::len).sum::<usize>() > MAX_TEXT {
            return Err("DOCX text exceeds 1 MiB".into());
        }
        buffer.clear();
    }
    Ok(paragraphs.join("\n\n"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Cursor, Write};

    fn docx(document: &str) -> Cursor<Vec<u8>> {
        let mut stream = Cursor::new(Vec::new());
        let mut writer = zip::ZipWriter::new(&mut stream);
        writer.start_file("word/document.xml", zip::write::SimpleFileOptions::default()).unwrap();
        writer.write_all(document.as_bytes()).unwrap();
        writer.finish().unwrap();
        stream.set_position(0);
        stream
    }

    #[test]
    fn reads_wordprocessing_paragraphs_and_skips_drawing_text() {
        let xml = r#"<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><w:body><w:p><w:r><w:t>Built </w:t></w:r><w:r><w:t>R&amp;D rigs &#8212; C++</w:t></w:r></w:p><a:p><a:t>chart label</a:t></a:p><w:p/><w:p><w:r><w:t>Second</w:t></w:r></w:p></w:body></w:document>"#;
        assert_eq!(text(docx(xml)).unwrap(), "Built R&D rigs \u{2014} C++\n\n\n\nSecond");
    }

    #[test]
    fn entity_declarations_and_non_archives_are_refused() {
        let xml = r#"<!DOCTYPE x [<!ENTITY a "aaaa">]><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>"#;
        assert_eq!(text(docx(xml)).unwrap_err(), "DOCX XML entities are not supported");
        assert!(text(Cursor::new(b"plain".to_vec())).is_err());
    }
}

//! PDF inspection in pure Rust: page limits, Unicode text in content order, PNG previews and
//! the source-order check that tells a person whether software can read their resume.

use hayro::hayro_interpret::font::{Glyph, GlyphRun};
use hayro::hayro_interpret::hayro_cmap::BfString;
use hayro::hayro_interpret::hayro_syntax::page::Page;
use hayro::hayro_interpret::hayro_syntax::{LoadPdfError, Pdf};
use hayro::hayro_interpret::{
    interpret_page, BlendMode, ClipPath, Context, Device, DrawMode, DrawProps, Image, ImageDrawProps,
    InterpreterCache, InterpreterSettings, SoftMask,
};
use hayro::kurbo::{Affine, BezPath, Point, Rect};
use hayro::vello_cpu::color::palette::css::WHITE;
use hayro::{render, PixmapSettings, RenderCache, RenderSettings};
use serde_json::{json, Value};
use unicode_normalization::UnicodeNormalization;

pub const MAX_PAGES: usize = 40;
const MAX_TEXT: usize = 1024 * 1024;
const MAX_SIDE_POINTS: f32 = 3000.0;

/// Collects glyph text in drawing order, inserting spaces at word gaps and newlines when the
/// baseline moves; PDFs from Typst and Chromium position words instead of drawing spaces.
#[derive(Default)]
struct Text {
    out: String,
    /// Previous glyph: start x, end x, baseline y and em size, in page space.
    last: Option<(f64, f64, f64, f64)>,
}

impl Text {
    fn push(&mut self, value: &str, start: Point, end: Point, em: f64) {
        if let Some((previous_start, previous_end, baseline, previous_em)) = self.last {
            let size = em.max(previous_em).max(0.1);
            let separated = self.out.ends_with(char::is_whitespace);
            if (start.y - baseline).abs() > 0.5 * size {
                self.out.truncate(self.out.trim_end_matches([' ', '\t']).len());
                if !self.out.ends_with('\n') {
                    self.out.push('\n');
                }
            } else if !separated && (start.x - previous_end > 0.18 * size || start.x < previous_start - size) {
                self.out.push(' ');
            }
        }
        self.out.push_str(value);
        self.last = Some((start.x, end.x, start.y, em));
    }
}

impl<'a> Device<'a> for Text {
    fn draw_path(&mut self, _: &BezPath, _: DrawProps<'a>, _: &DrawMode) {}
    fn push_clip_path(&mut self, _: &ClipPath) {}
    fn push_transparency_group(&mut self, _: f32, _: Option<SoftMask<'a>>, _: BlendMode) {}
    fn draw_image(&mut self, _: Image<'a, '_>, _: ImageDrawProps<'a>) {}
    fn pop_clip(&mut self) {}
    fn pop_transparency_group(&mut self) {}

    fn draw_glyph_run(&mut self, run: &GlyphRun<'_, 'a>, props: DrawProps<'a>, _: &DrawMode) {
        for glyph in run.glyphs() {
            // Glyph space has 1000 units per em.
            let transform: Affine = props.transform * glyph.transform();
            let start = transform * Point::ZERO;
            let em = (transform * Point::new(1000.0, 0.0) - start).hypot();
            let advance = match &**glyph {
                Glyph::Outline(outline) => outline.advance_width(),
                Glyph::Type3(type3) => type3.advance_width(),
            }
            .unwrap_or(500.0) as f64;
            let end = transform * Point::new(advance, 0.0);
            match glyph.as_unicode() {
                Some(BfString::Char(c)) => self.push(&c.to_string(), start, end, em),
                Some(BfString::String(s)) => self.push(&s, start, end, em),
                None => self.push("\u{FFFD}", start, end, em),
            }
        }
    }
}

fn page_text<'a>(page: &'a Page<'a>, cache: &'a InterpreterCache<'a>) -> String {
    let mut context = Context::new(Affine::IDENTITY, Rect::new(0.0, 0.0, 1.0, 1.0), cache, page.xref(),
                                   InterpreterSettings::default());
    let mut text = Text::default();
    interpret_page(page, &mut context, &mut text);
    text.out
}

pub fn open(data: Vec<u8>) -> Result<Pdf, String> {
    let pdf = Pdf::new(data).map_err(|error| match error {
        LoadPdfError::Decryption(_) => "Encrypted PDFs require an unlocked local copy".to_string(),
        _ => "The file is not a readable PDF".to_string(),
    })?;
    if !(1..=MAX_PAGES).contains(&pdf.pages().len()) {
        return Err(format!("PDF must have 1–{MAX_PAGES} pages"));
    }
    for page in pdf.pages().iter() {
        let (width, height) = page.render_dimensions();
        if width <= 0.0 || height <= 0.0 || width.max(height) > MAX_SIDE_POINTS {
            return Err("PDF page dimensions exceed preview limits".into());
        }
    }
    Ok(pdf)
}

/// Each page's text, in content order.
pub fn texts(pdf: &Pdf) -> Vec<String> {
    let cache = InterpreterCache::new();
    pdf.pages().iter().map(|page| page_text(page, &cache)).collect()
}

/// A white-background PNG of a page at `scale` pixels per point.
pub fn png<'a>(page: &'a Page<'a>, cache: &RenderCache<'a>, scale: f32) -> Result<Vec<u8>, String> {
    let settings = PixmapSettings { x_scale: scale, y_scale: scale, bg_color: WHITE };
    render(page, cache, &InterpreterSettings::default(), &RenderSettings::default(), &settings)
        .into_png()
        .map_err(|e| format!("Cannot encode the page preview: {e}"))
}

pub fn preview_scale(page: &Page<'_>) -> f32 {
    let (width, height) = page.render_dimensions();
    (1800.0 / width.max(height)).min(1.5)
}

pub fn check_text_size(texts: &[String]) -> Result<(), String> {
    if texts.iter().map(String::len).sum::<usize>() > MAX_TEXT {
        Err("Extracted PDF text exceeds 1 MiB".into())
    } else {
        Ok(())
    }
}

fn normalize(value: &str) -> String {
    value.nfkc().filter(|c| !c.is_whitespace()).flat_map(char::to_lowercase).collect()
}

/// The readability report: whether text is selectable and, for HTML resumes whose layout the
/// worker recorded, whether the PDF text follows the source order.
pub fn checks(extracted: &str, mut warnings: Vec<String>, layout: Option<&Value>) -> Value {
    let mut reading_order = "not_checked";
    let mut overflow = "inspect_preview";
    if let Some(layout) = layout {
        let haystack = normalize(extracted);
        let mut cursor = 0;
        let mut missing = Vec::new();
        for chunk in layout["chunks"].as_array().into_iter().flatten().filter_map(Value::as_str) {
            let needle = normalize(chunk);
            match haystack[cursor..].find(&needle) {
                Some(position) => cursor += position + needle.len(),
                None => missing.push(chunk.chars().take(90).collect::<String>()),
            }
        }
        reading_order = if missing.is_empty() { "source_order_matches" } else { "warning" };
        warnings.extend(missing.iter().take(40).map(|c| format!("PDF text missing or out of source order: {c}")));
        let observed: Vec<String> = layout["warnings"].as_array().into_iter().flatten()
            .filter_map(Value::as_str).map(String::from).collect();
        overflow = if observed.iter().any(|w| w.contains("overflow")) { "warning" } else { "no_horizontal_overflow_detected" };
        warnings.extend(observed.into_iter().take(40));
    }
    if extracted.trim().is_empty() {
        warnings.push("No selectable PDF text; this is not an ATS-readable text resume".into());
    }
    json!({"extractable_text": !extracted.trim().is_empty(), "extracted_text": extracted,
           "reading_order": reading_order, "overflow": overflow, "ats_guarantee": false,
           "warnings": warnings, "scale": 1})
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A minimal one-page PDF using the standard Helvetica font and the given content stream.
    fn pdf(content: &str) -> Vec<u8> {
        let objects = [
            "<< /Type /Catalog /Pages 2 0 R >>".to_string(),
            "<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_string(),
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>".to_string(),
            format!("<< /Length {} >>\nstream\n{content}\nendstream", content.len()),
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>".to_string(),
        ];
        let mut out = b"%PDF-1.4\n".to_vec();
        let mut offsets = Vec::new();
        for (index, object) in objects.iter().enumerate() {
            offsets.push(out.len());
            out.extend(format!("{} 0 obj\n{object}\nendobj\n", index + 1).bytes());
        }
        let xref = out.len();
        out.extend(format!("xref\n0 {}\n0000000000 65535 f \n", objects.len() + 1).bytes());
        for offset in offsets {
            out.extend(format!("{offset:010} 00000 n \n").bytes());
        }
        out.extend(format!("trailer\n<< /Size {} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n", objects.len() + 1).bytes());
        out
    }

    #[test]
    fn positioned_words_get_spaces_and_moved_baselines_get_newlines() {
        let document = open(pdf("BT /F1 12 Tf 72 700 Td (Built) Tj 40 0 Td (firmware) Tj 0 -20 Td (Tested) Tj ET")).unwrap();
        assert_eq!(texts(&document), ["Built firmware\nTested"]);
    }

    #[test]
    fn adjacent_runs_on_one_word_are_not_split() {
        let document = open(pdf("BT /F1 12 Tf 72 700 Td (Fir) Tj (mware) Tj ET")).unwrap();
        assert_eq!(texts(&document), ["Firmware"]);
    }

    #[test]
    fn pages_render_to_png_and_garbage_is_refused() {
        let document = open(pdf("BT /F1 12 Tf 72 700 Td (Hi) Tj ET")).unwrap();
        let page = &document.pages()[0];
        let image = png(page, &RenderCache::new(), preview_scale(page)).unwrap();
        assert!(image.starts_with(b"\x89PNG\r\n\x1a\n"));
        assert_eq!(open(b"not a pdf".to_vec()).err().unwrap(), "The file is not a readable PDF");
    }

    #[test]
    fn source_order_check_flags_missing_or_reordered_text() {
        let layout = json!({"chunks": ["Synthetic Applicant", "Built a rig"], "warnings": []});
        assert_eq!(checks("Synthetic  Applicant\nBuilt a rig", vec![], Some(&layout))["reading_order"], "source_order_matches");
        let flipped = checks("Built a rig\nSynthetic Applicant", vec![], Some(&layout));
        assert_eq!(flipped["reading_order"], "warning");
        assert_eq!(checks("", vec![], None)["extractable_text"], false);
    }
}

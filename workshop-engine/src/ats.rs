//! The automatic ATS check: what a resume parser reads from the PDF's text layer.
//!
//! Every compile runs it. It reports the contact details and standard sections a parser
//! can find, and the layout and character problems that commonly scramble parsing
//! (columns, unmapped glyphs, icon fonts, tiny text, length, undated experience). It is a
//! readability report, not a score: employers' systems differ and nothing here predicts
//! a callback.

use serde_json::{json, Value};

use crate::pdf::{Line, PageText};

/// Headings parsers commonly recognise, by the section they open.
const SECTIONS: &[(&str, &[&str])] = &[
    ("experience", &["experience", "work experience", "professional experience", "relevant experience",
                     "research experience", "employment", "employment history", "work history", "internships",
                     "internship experience"]),
    ("education", &["education", "academic background", "education and training"]),
    ("skills", &["skills", "technical skills", "core skills", "skills and interests", "skills & interests",
                 "technologies", "technical proficiencies"]),
    ("projects", &["projects", "personal projects", "selected projects", "academic projects", "technical projects"]),
    ("summary", &["summary", "profile", "objective", "professional summary", "about me"]),
    ("certifications", &["certifications", "certificates", "licenses and certifications", "licenses & certifications"]),
    ("awards", &["awards", "honors", "honors and awards", "honors & awards", "awards and honors"]),
    ("publications", &["publications"]),
    ("leadership", &["leadership", "activities", "leadership and activities", "leadership & activities",
                     "extracurricular activities", "volunteer experience", "volunteering"]),
];

fn heading(text: &str) -> Option<&'static str> {
    let cleaned = text.trim().trim_end_matches(':').trim().to_lowercase();
    if cleaned.is_empty() || cleaned.split_whitespace().count() > 5 {
        return None;
    }
    let cleaned = cleaned.split_whitespace().collect::<Vec<_>>().join(" ");
    SECTIONS.iter().find(|(_, names)| names.contains(&cleaned.as_str())).map(|(kind, _)| *kind)
}

fn dated(text: &str) -> bool {
    let lowered = text.to_lowercase();
    lowered.contains("present") || lowered.contains("current")
        || text.as_bytes().windows(4).enumerate().any(|(i, w)| {
            (w.starts_with(b"19") || w.starts_with(b"20")) && w.iter().all(u8::is_ascii_digit)
                && !text.as_bytes().get(i + 4).is_some_and(u8::is_ascii_digit)
                && !(i > 0 && text.as_bytes()[i - 1].is_ascii_digit())
        })
}

fn tokens(text: &str) -> impl Iterator<Item = &str> {
    text.split(|c: char| c.is_whitespace() || "|,;·•<>()".contains(c))
        .map(|t| t.trim_matches(|c: char| ".:'\"[]".contains(c)))
        .filter(|t| !t.is_empty())
}

fn email(text: &str) -> Option<String> {
    tokens(text).find(|t| {
        let mut parts = t.splitn(2, '@');
        let (local, domain) = (parts.next().unwrap_or(""), parts.next().unwrap_or(""));
        !local.is_empty() && domain.contains('.') && !domain.starts_with('.') && !domain.ends_with('.')
    }).map(|t| t.trim_start_matches("mailto:").to_string())
}

fn phone(text: &str) -> Option<String> {
    let mut run = String::new();
    let mut found = None;
    for character in text.chars().chain(std::iter::once('\n')) {
        if character.is_ascii_digit() || "+()-. ".contains(character) {
            run.push(character);
            continue;
        }
        let candidate = run.trim().trim_matches(|c: char| "-.".contains(c)).trim();
        let digits = candidate.chars().filter(char::is_ascii_digit).count();
        if (10..=15).contains(&digits) && candidate.len() <= 22 {
            found = Some(candidate.to_string());
            break;
        }
        run.clear();
    }
    found
}

fn links(text: &str) -> Vec<String> {
    let mut found: Vec<String> = Vec::new();
    for token in tokens(text) {
        let lowered = token.to_lowercase();
        let link = lowered.starts_with("http://") || lowered.starts_with("https://") || lowered.starts_with("www.")
            || ["linkedin.com/", "github.com/", "gitlab.com/"].iter().any(|host| lowered.contains(host));
        if link && !token.contains('@') && !found.iter().any(|f| f == token) && found.len() < 6 {
            found.push(token.to_string());
        }
    }
    found
}

/// The largest line among the first few on page one, when it reads like a name.
fn name(pages: &[PageText]) -> Option<String> {
    let first = pages.first()?.lines.iter().take(4).max_by(|a, b| a.size.total_cmp(&b.size))?;
    let text = first.text();
    let words = text.split_whitespace().count();
    ((1..=5).contains(&words) && !text.contains('@') && !text.chars().any(|c| c.is_ascii_digit())
        && heading(&text).is_none()).then_some(text)
}

/// Lines where two or more segments are each running text (four or more words) and none
/// is a date: a second column, not a right-aligned date or place.
fn column_lines(lines: &[Line]) -> usize {
    lines.iter().filter(|line| {
        let long: Vec<_> = line.segments.iter().filter(|s| s.text.split_whitespace().count() >= 4).collect();
        long.len() >= 2 && !line.segments.iter().any(|s| dated(&s.text))
    }).count()
}

fn finding(id: &str, level: &str, title: impl Into<String>, detail: impl Into<String>) -> Value {
    json!({"id": id, "level": level, "title": title.into(), "detail": detail.into()})
}

pub fn report(pages: &[PageText]) -> Value {
    let all: String = pages.iter().map(|p| p.text.as_str()).collect::<Vec<_>>().join("\n");
    let mut findings = Vec::new();
    let mut sections: Vec<(String, &'static str, usize, bool)> = Vec::new();
    for line in pages.iter().flat_map(|p| p.lines.iter()) {
        let text = line.text();
        if let Some(kind) = heading(&text) {
            sections.push((text, kind, 0, false));
        } else if let Some(current) = sections.last_mut() {
            current.2 += 1;
            current.3 |= dated(&text);
        }
    }
    let contact = json!({"name": name(pages), "email": email(&all), "phone": phone(&all), "links": links(&all)});
    if all.trim().is_empty() {
        findings.push(finding("text", "fail", "No selectable text",
            "A parser reads nothing from this PDF. Export from the source instead of scanning or flattening it."));
    } else {
        findings.push(finding("text", "pass", "Selectable text", "Every page has a text layer a parser can read."));
        findings.push(match contact["name"].as_str() {
            Some(found) => finding("name", "pass", format!("Name read as \"{found}\""), "The largest line at the top is taken as your name."),
            None => finding("name", "warn", "No name line found", "Put your name alone on the first line, larger than the rest."),
        });
        findings.push(match contact["email"].as_str() {
            Some(found) => finding("email", "pass", format!("Email read as {found}"), "Written out as text, so a parser can copy it."),
            None => finding("email", "warn", "No email address in the text", "Write your email as plain text; a link icon alone is not read."),
        });
        findings.push(match contact["phone"].as_str() {
            Some(found) => finding("phone", "pass", format!("Phone read as {found}"), "Ten or more digits found together."),
            None => finding("phone", "warn", "No phone number in the text", "Add one as plain digits if you want calls."),
        });
        let kinds: Vec<&str> = sections.iter().map(|s| s.1).collect();
        let mut missing = Vec::new();
        if !kinds.contains(&"experience") && !kinds.contains(&"projects") {
            missing.push("Experience");
        }
        if !kinds.contains(&"education") {
            missing.push("Education");
        }
        if !kinds.contains(&"skills") {
            missing.push("Skills");
        }
        let titles: Vec<&str> = sections.iter().map(|s| s.0.as_str()).collect();
        findings.push(if missing.is_empty() {
            finding("sections", "pass", "Standard section headings", format!("Recognised: {}.", titles.join(", ")))
        } else {
            finding("sections", "warn", format!("No {} heading recognised", missing.join(", ")),
                "Parsers sort content by standard headings such as Experience, Education and Skills; creative names can leave content unfiled.")
        });
        let undated = sections.iter().any(|s| s.1 == "experience" && s.2 > 0 && !s.3);
        if kinds.contains(&"experience") {
            findings.push(if undated {
                finding("dates", "warn", "Experience without dates", "Give each role a year or date range (\"May 2025 – Aug 2025\").")
            } else {
                finding("dates", "pass", "Experience is dated", "Dates sit with each role.")
            });
        }
        let column_pages: Vec<String> = pages.iter().enumerate()
            .filter(|(_, page)| column_lines(&page.lines) >= 4).map(|(i, _)| (i + 1).to_string()).collect();
        findings.push(if column_pages.is_empty() {
            finding("columns", "pass", "Single reading order", "No side-by-side columns of running text.")
        } else {
            finding("columns", "warn", format!("Two columns on page {}", column_pages.join(", ")),
                "Many parsers read straight across the page and interleave columns. One column is safest.")
        });
        let unmapped: usize = pages.iter().map(|p| p.unmapped).sum();
        let private: usize = pages.iter().map(|p| p.private_use).sum();
        findings.push(if unmapped + private == 0 {
            finding("characters", "pass", "Every character is readable", "All glyphs map to real text.")
        } else {
            finding("characters", "warn", format!("{} unreadable characters", unmapped + private),
                "Some glyphs have no text meaning (icon fonts or fonts without a Unicode map); a parser sees blanks or symbols.")
        });
        let small: usize = pages.iter().map(|p| p.small).sum();
        if small > 20 {
            findings.push(finding("small_text", "warn", "Text under 7.5 pt",
                format!("{small} letters or digits are very small; recruiters and some parsers skip them.")));
        }
        findings.push(if pages.len() > 2 {
            finding("length", "warn", format!("{} pages", pages.len()), "Most early-career resumes fit on one page; two at most.")
        } else {
            finding("length", "pass", format!("{} page{}", pages.len(), if pages.len() == 1 { "" } else { "s" }), "A typical length.")
        });
    }
    let count = |level: &str| findings.iter().filter(|f| f["level"] == level).count();
    json!({
        "contact": contact,
        "sections": sections.iter().map(|(title, kind, lines, _)| json!({"title": title, "kind": kind, "lines": lines})).collect::<Vec<_>>(),
        "findings": findings,
        "counts": {"pass": count("pass"), "warn": count("warn"), "fail": count("fail")},
        "note": "What a typical resume parser reads from this PDF. Employers' systems differ; this is not a score and does not predict a callback.",
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::pdf::{open, pages, tests::pdf};

    fn ats(content: &str) -> Value {
        report(&pages(&open(pdf(content)).unwrap()))
    }

    fn level(report: &Value, id: &str) -> String {
        report["findings"].as_array().unwrap().iter().find(|f| f["id"] == id)
            .map(|f| f["level"].as_str().unwrap().to_string()).unwrap_or_default()
    }

    const CLEAN: &str = "BT /F1 20 Tf 72 740 Td (Alex Rivera) Tj /F1 10 Tf 0 -16 Td (alex@example.test | 512-555-0100 | github.com/alex) Tj \
        /F1 12 Tf 0 -24 Td (Experience) Tj /F1 10 Tf 0 -14 Td (Firmware Intern, Example Robotics) Tj 330 0 Td (May 2026 -- Aug 2026) Tj \
        -330 -14 Td (Built a test rig in C++.) Tj /F1 12 Tf 0 -24 Td (Education) Tj /F1 10 Tf 0 -14 Td (B.S. Electrical Engineering) Tj \
        /F1 12 Tf 0 -24 Td (Technical Skills) Tj /F1 10 Tf 0 -14 Td (C++, Rust, Python) Tj ET";

    #[test]
    fn a_clean_single_column_resume_reads_fully() {
        let report = ats(CLEAN);
        assert_eq!(report["contact"]["name"], "Alex Rivera");
        assert_eq!(report["contact"]["email"], "alex@example.test");
        assert_eq!(report["contact"]["phone"], "512-555-0100");
        assert_eq!(report["contact"]["links"], json!(["github.com/alex"]));
        let kinds: Vec<&str> = report["sections"].as_array().unwrap().iter().map(|s| s["kind"].as_str().unwrap()).collect();
        assert_eq!(kinds, ["experience", "education", "skills"]);
        assert_eq!(report["counts"]["warn"], 0, "{report:#}");
        assert_eq!(level(&report, "dates"), "pass");
    }

    #[test]
    fn side_by_side_running_text_is_a_column_warning_but_right_aligned_dates_are_not() {
        let mut two = String::from("BT /F1 10 Tf 50 740 Td (Experience) Tj");
        for _ in 0..5 {
            two.push_str(" 0 -14 Td (Designed and built test rigs here) Tj 280 0 Td (Led the robotics club outreach team) Tj -280 0 Td");
        }
        two.push_str(" ET");
        assert_eq!(level(&ats(&two), "columns"), "warn");
        let mut dates = String::from("BT /F1 10 Tf 50 740 Td (Education) Tj");
        for _ in 0..5 {
            dates.push_str(" 0 -14 Td (Bachelor of Arts in Computer Science) Tj 330 0 Td (Aug. 2018 -- May 2021) Tj -330 0 Td");
        }
        dates.push_str(" ET");
        assert_eq!(level(&ats(&dates), "columns"), "pass");
    }

    #[test]
    fn missing_contact_sections_and_dates_are_reported() {
        let report = ats("BT /F1 10 Tf 72 740 Td (My Journey) Tj 0 -14 Td (Experience) Tj 0 -14 Td (Did many things for a while) Tj ET");
        assert_eq!(level(&report, "email"), "warn");
        assert_eq!(level(&report, "phone"), "warn");
        assert_eq!(level(&report, "sections"), "warn");
        assert_eq!(level(&report, "dates"), "warn");
        assert!(report["findings"].as_array().unwrap().iter().any(|f| f["title"].as_str().unwrap().contains("Education, Skills")));
    }

    #[test]
    fn date_ranges_are_not_phone_numbers() {
        assert_eq!(phone("2018 -- 2021 and 2022-2023"), None);
        assert_eq!(phone("Call +1 (512) 555-0100 today").as_deref(), Some("+1 (512) 555-0100"));
    }
}

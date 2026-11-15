//! The in-sandbox half. Runs only inside bubblewrap on `/job`; the host verifies its output.

use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::time::{Duration, Instant};

use base64::Engine as _;
use serde_json::{json, Value};

use crate::{cdp, docx, pdf};

const MAX_BYTES: u64 = 16 * 1024 * 1024;

pub fn main() -> ! {
    match operate() {
        Ok(()) => std::process::exit(0),
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(1);
        }
    }
}

fn limit(resource: libc::__rlimit_resource_t, soft: u64, hard: u64) -> Result<(), String> {
    let value = libc::rlimit { rlim_cur: soft, rlim_max: hard };
    // SAFETY: setrlimit reads one initialized struct.
    if unsafe { libc::setrlimit(resource, &value) } == 0 { Ok(()) } else { Err("Cannot apply sandbox resource limits".into()) }
}

fn limits() -> Result<(), String> {
    limit(libc::RLIMIT_CPU, 80, 85)?;
    limit(libc::RLIMIT_FSIZE, MAX_BYTES, MAX_BYTES)?;
    limit(libc::RLIMIT_NOFILE, 256, 256)?;
    limit(libc::RLIMIT_CORE, 0, 0)?;
    // Chromium reserves a huge virtual address space; bound actual data instead.
    limit(libc::RLIMIT_DATA, 2 << 30, 2 << 30)?;
    limit(libc::RLIMIT_NPROC, 128, 128)
}

fn operate() -> Result<(), String> {
    limits()?;
    let request: Value = serde_json::from_slice(&std::fs::read("/job/request.json").map_err(|e| e.to_string())?)
        .map_err(|_| "Unreadable worker request".to_string())?;
    match request["mode"].as_str() {
        Some("engine") => engine(&request),
        Some("html") => html(&request),
        Some("pdf") => inspect(&request),
        Some("docx") => {
            let file = std::fs::File::open("/job/input.docx").map_err(|e| e.to_string())?;
            write_json("/job/result.json", &json!({"text": docx::text(file)?}))
        }
        _ => Err("Unknown private worker operation".into()),
    }
}

fn write_json(path: &str, value: &Value) -> Result<(), String> {
    std::fs::write(path, value.to_string()).map_err(|e| e.to_string())
}

fn wait(mut child: Child, timeout: Duration, what: &str) -> Result<ExitStatus, String> {
    let deadline = Instant::now() + timeout;
    loop {
        if let Some(status) = child.try_wait().map_err(|e| e.to_string())? {
            return Ok(status);
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            return Err(format!("{what} exceeded its time limit"));
        }
        std::thread::sleep(Duration::from_millis(20));
    }
}

fn strings(value: &Value) -> Result<Vec<String>, String> {
    value.as_array().filter(|items| !items.is_empty()).ok_or("Missing command")?
        .iter().map(|item| item.as_str().map(String::from).ok_or_else(|| "Invalid command".to_string())).collect()
}

/// A compiler run; its own output goes to the host's diagnostics log.
fn engine(request: &Value) -> Result<(), String> {
    let command = strings(&request["command"])?;
    let child = Command::new(&command[0]).args(&command[1..]).stdin(Stdio::null()).spawn()
        .map_err(|e| format!("Cannot start the compiler: {e}"))?;
    let status = wait(child, Duration::from_secs(70), "Compiler")?;
    if status.success() { Ok(()) } else { Err(format!("Compiler exited with status {}", status.code().unwrap_or(-1))) }
}

const PAGE_RULES: &str = r#"(() => {const out=[]; function visit(rules) {
  for(const r of rules) {if(r.type===6) out.push({selector:r.selectorText,
  size:r.style.getPropertyValue('size'), margins:['top','right','bottom','left'].map(
  x=>r.style.getPropertyValue('margin-'+x))});
  else if(r.cssRules && (!r.conditionText || matchMedia(r.conditionText).matches)) visit(r.cssRules);}}
  for(const s of document.styleSheets) visit(s.cssRules);return out;})()"#;

const LAYOUT: &str = r#"(() => {const chunks=[], warnings=[];
  const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT); let node;
  while(node=walker.nextNode()) {const text=node.textContent.trim();
    if(!text || ['STYLE','SCRIPT'].includes(node.parentElement.tagName)) continue;
    chunks.push(text); const range=document.createRange();range.selectNodeContents(node);
    const boxes=[...range.getClientRects()], style=getComputedStyle(node.parentElement);
    if(!boxes.length || style.visibility!=='visible' || Number(style.opacity)===0)
      warnings.push('Hidden text: '+text.slice(0,80));
    if(parseFloat(style.fontSize)<10.66) warnings.push('Text smaller than 8pt: '+text.slice(0,80));
    if(boxes.some(r=>r.left < -1 || r.right > innerWidth+1))
      warnings.push('Horizontal overflow: '+text.slice(0,80));
  } return {chunks,warnings};})()"#;

/// Print the frozen, already-audited HTML with scripts and network disabled, recording the
/// text order and layout warnings the PDF check compares against.
fn html(request: &Value) -> Result<(), String> {
    let binary = request["binary"].as_str().ok_or("Missing Chromium path")?;
    let profile = PathBuf::from("/tmp/chromium");
    let log = std::fs::File::create("/job/browser.log").map_err(|e| e.to_string())?;
    let mut child = Command::new(binary)
        .args(["--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
               "--disable-background-networking", "--disable-component-update", "--disable-sync",
               "--no-first-run", "--disable-extensions", "--disable-breakpad",
               "--host-resolver-rules=MAP * ~NOTFOUND", "--remote-debugging-port=0",
               "--remote-debugging-address=127.0.0.1"])
        .arg(format!("--user-data-dir={}", profile.display()))
        .arg("about:blank")
        .stdin(Stdio::null())
        .stdout(log.try_clone().map_err(|e| e.to_string())?)
        .stderr(log)
        .spawn()
        .map_err(|e| format!("Cannot start Chromium: {e}"))?;
    let outcome = print(&mut child, &profile);
    let _ = child.kill();
    let _ = child.wait();
    outcome
}

fn print(child: &mut Child, profile: &Path) -> Result<(), String> {
    let deadline = Instant::now() + Duration::from_secs(35);
    let marker = profile.join("DevToolsActivePort");
    while !marker.exists() {
        if child.try_wait().map_err(|e| e.to_string())?.is_some() || Instant::now() > deadline {
            let log = std::fs::read("/job/browser.log").unwrap_or_default();
            let tail = String::from_utf8_lossy(&log[log.len().saturating_sub(2000)..]).into_owned();
            return Err(format!("Chromium did not start: {tail}"));
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    let port: u16 = std::fs::read_to_string(&marker).map_err(|e| e.to_string())?
        .lines().next().and_then(|line| line.trim().parse().ok()).ok_or("Chromium reported no debugging port")?;
    let mut cdp = cdp::Cdp::connect(port)?;
    cdp.call("Page.enable", json!({}))?;
    cdp.call("Network.enable", json!({}))?;
    cdp.call("Network.setBlockedURLs", json!({"urls": ["*"]}))?;
    cdp.call("Emulation.setScriptExecutionDisabled", json!({"value": true}))?;
    cdp.call("Emulation.setEmulatedMedia", json!({"media": "print"}))?;
    let frame = cdp.call("Page.getFrameTree", json!({}))?["frameTree"]["frame"]["id"].clone();
    let html = std::fs::read_to_string("/job/frozen.html").map_err(|e| e.to_string())?;
    cdp.call("Page.setDocumentContent", json!({"frameId": frame, "html": html}))?;
    let (width, height, [top, right, bottom, left]) = cdp::geometry(&cdp.evaluate(PAGE_RULES)?)?;
    cdp.call("Emulation.setDeviceMetricsOverride", json!({
        "width": ((width - left - right) * 96.0).round(), "height": ((height - top - bottom) * 96.0).round(),
        "deviceScaleFactor": 1, "mobile": false}))?;
    cdp.evaluate("document.fonts.ready.then(() => true)")?;
    let layout = cdp.evaluate(LAYOUT)?;
    let printed = cdp.call("Page.printToPDF", json!({
        "printBackground": true, "displayHeaderFooter": false, "preferCSSPageSize": true, "scale": 1,
        "paperWidth": width, "paperHeight": height, "marginTop": top, "marginRight": right,
        "marginBottom": bottom, "marginLeft": left}))?;
    let data = base64::engine::general_purpose::STANDARD
        .decode(printed["data"].as_str().ok_or("Chromium returned no PDF")?)
        .map_err(|_| "Chromium returned an unreadable PDF".to_string())?;
    std::fs::write("/job/output.pdf", data).map_err(|e| e.to_string())?;
    write_json("/job/layout.json", &layout)
}

fn ocr(tesseract: &str, image: &str) -> Result<String, String> {
    let child = Command::new(tesseract).args([image, "stdout"]).stdin(Stdio::null())
        .stdout(Stdio::piped()).stderr(Stdio::piped()).spawn()
        .map_err(|e| format!("Cannot start Tesseract: {e}"))?;
    let output = {
        let started = Instant::now();
        let mut child = child;
        loop {
            if child.try_wait().map_err(|e| e.to_string())?.is_some() {
                break child.wait_with_output().map_err(|e| e.to_string())?;
            }
            if started.elapsed() > Duration::from_secs(20) {
                let _ = child.kill();
                let _ = child.wait();
                return Err("Local OCR exceeded its time limit".into());
            }
            std::thread::sleep(Duration::from_millis(20));
        }
    };
    if !output.status.success() {
        let detail = String::from_utf8_lossy(&output.stderr);
        return Err(format!("Local OCR failed; install Tesseract language data: {}", &detail[detail.len().saturating_sub(1000)..]));
    }
    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
}

fn inspect(request: &Value) -> Result<(), String> {
    let input = request["input"].as_str().filter(|name| !name.contains('/')).ok_or("Invalid PDF input")?;
    let document = pdf::open(std::fs::read(Path::new("/job").join(input)).map_err(|e| e.to_string())?)?;
    let layer = pdf::pages(&document);
    // What a parser reads is the PDF's own text layer, so the ATS check runs before OCR.
    let ats = crate::ats::report(&layer);
    let mut texts: Vec<String> = layer.into_iter().map(|page| page.text).collect();
    let mut warnings = Vec::new();
    let cache = hayro::RenderCache::new();
    if request["ocr"].as_bool() == Some(true) {
        for (index, page) in document.pages().iter().enumerate() {
            if !texts[index].trim().is_empty() {
                continue;
            }
            let tesseract = request["tesseract"].as_str()
                .ok_or("PDF has no selectable text; install Tesseract and language data or provide source")?;
            let image = format!("/job/ocr-{index}.png");
            std::fs::write(&image, pdf::png(page, &cache, 1.5)?).map_err(|e| e.to_string())?;
            texts[index] = ocr(tesseract, &image)?;
            warnings.push(format!("Page {} reconstructed with OCR; verify every character", index + 1));
        }
    }
    pdf::check_text_size(&texts)?;
    if request["raster"].as_bool() == Some(true) {
        let mut total = 0u64;
        for (index, page) in document.pages().iter().enumerate() {
            let image = pdf::png(page, &cache, pdf::preview_scale(page))?;
            total += image.len() as u64;
            if total > MAX_BYTES {
                return Err("Raster previews exceed 16 MiB".into());
            }
            std::fs::write(format!("/job/page-{}.png", index + 1), image).map_err(|e| e.to_string())?;
        }
    }
    let layout = std::fs::read("/job/layout.json").ok().and_then(|raw| serde_json::from_slice::<Value>(&raw).ok());
    let checks = pdf::checks(&texts.join("\n\n"), warnings, layout.as_ref(), ats);
    write_json("/job/result.json", &json!({"pages": document.pages().len(), "checks": checks}))
}

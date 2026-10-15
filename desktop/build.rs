fn main() {
    println!("cargo:rustc-env=KITTY_TARGET_TRIPLE={}", std::env::var("TARGET").unwrap());
    tauri_build::build();
}

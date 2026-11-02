{
  description = "rika! Linux desktop build prerequisites";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      eachSystem = nixpkgs.lib.genAttrs systems;
    in {
      devShells = eachSystem (system:
        let
          pkgs = import nixpkgs { inherit system; };
          libraries = with pkgs; [
            gtk3 webkitgtk_4_1 libsoup_3 librsvg libayatana-appindicator
            openssl glib zlib stdenv.cc.cc.lib libxcrypt libGL
            gst_all_1.gstreamer gst_all_1.gst-plugins-base gst_all_1.gst-plugins-good
          ];
        in rec {
          default = pkgs.mkShell {
            nativeBuildInputs = with pkgs; [
              cargo rustc rustfmt pkg-config uv nodejs_22 patchelf
              gnumake gcc binutils dpkg fakeroot file wget desktop-file-utils
            ];
            buildInputs = libraries;
            LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath libraries;
            NIX_LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath libraries;
            GST_PLUGIN_SYSTEM_PATH_1_0 = pkgs.lib.makeSearchPath "lib/gstreamer-1.0" [
              pkgs.gst_all_1.gst-plugins-base pkgs.gst_all_1.gst-plugins-good
            ];
            NIX_LD = pkgs.lib.fileContents "${pkgs.stdenv.cc}/nix-support/dynamic-linker";
            APPIMAGE_EXTRACT_AND_RUN = "1";
            shellHook = ''
              export XDG_DATA_DIRS="${pkgs.gsettings-desktop-schemas}/share/gsettings-schemas/${pkgs.gsettings-desktop-schemas.name}:${pkgs.gtk3}/share/gsettings-schemas/${pkgs.gtk3.name}:''${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
              printf 'rika! desktop dependencies ready; run bash scripts/build-desktop.sh from the project root.\n'
              printf 'NixOS AppImage tooling uses the host nix-ld loader with this shell’s NIX_LD environment.\n'
            '';
          };
          # Reuse an existing host Rust toolchain without downloading a second one.
          # Native libraries and packaging tools still use the locked nixpkgs.
          host-toolchain = default.overrideAttrs (old: {
            nativeBuildInputs = builtins.filter
              (input: !(builtins.elem input [ pkgs.cargo pkgs.rustc pkgs.rustfmt ]))
              old.nativeBuildInputs;
          });
        });
    };
}

// MathLive's package exports no sounds subpath, so its bundled files are imported by path.
import keypress from "../../../node_modules/mathlive/sounds/keypress-standard.wav?url";
import spacebar from "../../../node_modules/mathlive/sounds/keypress-spacebar.wav?url";
import enter from "../../../node_modules/mathlive/sounds/keypress-return.wav?url";
import remove from "../../../node_modules/mathlive/sounds/keypress-delete.wav?url";
import plonk from "../../../node_modules/mathlive/sounds/plonk.wav?url";

/**
 * MathLive's `<math-field>`, loaded only when an expression answer is asked for. Nothing
 * comes from a CDN: its glyphs are the KaTeX fonts the app already bundles (main.ts), so
 * MathLive loads no fonts of its own, and its sounds are bundled files on this origin.
 */
let ready: Promise<void> | null = null;

export function loadMathfield(): Promise<void> {
  ready ??= import("mathlive").then(({ MathfieldElement }) => {
    MathfieldElement.fontsDirectory = null;
    // MathLive joins `${directory}/${file}`; an absolute origin keeps it from probing a script URL.
    MathfieldElement.soundsDirectory = location.origin;
    const file = (url: string) => new URL(url, location.href).pathname.replace(/^\//, "");
    MathfieldElement.keypressSound = { default: file(keypress), spacebar: file(spacebar), return: file(enter), delete: file(remove) };
    MathfieldElement.plonkSound = file(plonk);
  });
  return ready;
}

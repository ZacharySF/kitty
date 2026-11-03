import type { ThemeModule } from "../contract";
import Art from "./Art.svelte";
import EmptyArt from "./EmptyArt.svelte";
import LoaderArt from "./LoaderArt.svelte";
import "./speed.css";

/** SPEED's module: hand-inked paper sheets, ink splat bullets, brush dividers, a ticking gear. */
const speed: ThemeModule = { Art, EmptyArt, LoaderArt };

export default speed;

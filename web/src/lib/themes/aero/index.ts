import type { ThemeModule } from "../contract";
import Art from "./Art.svelte";
import EmptyArt from "./EmptyArt.svelte";
import LoaderArt from "./LoaderArt.svelte";
import "./aero.css";

/** Kitty's module: a night concourse behind aqua glass, different every time it loads. */
const aero: ThemeModule = { Art, EmptyArt, LoaderArt };

export default aero;

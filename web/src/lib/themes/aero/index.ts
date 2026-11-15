import type { ThemeModule } from "../contract";
import Art from "./Art.svelte";
import EmptyArt from "./EmptyArt.svelte";
import LoaderArt from "./LoaderArt.svelte";
import "./aero.css";

/** Kitty's module: a film still behind flat panes, set like a record sleeve; a different frame on every load. */
const aero: ThemeModule = { Art, EmptyArt, LoaderArt };

export default aero;

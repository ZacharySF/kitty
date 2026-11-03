import type { ThemeModule } from "../contract";
import Art from "./Art.svelte";
import EmptyArt from "./EmptyArt.svelte";
import Frame from "./Frame.svelte";
import LoaderArt from "./LoaderArt.svelte";
import "./c.css";

/** c's module: an engraving on black, rope-and-knot frame, sigil bullets, diamond ticks. */
const c: ThemeModule = { Art, EmptyArt, LoaderArt, Frame };

export default c;

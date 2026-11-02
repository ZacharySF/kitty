import type { ThemeModule } from "../contract";
import Art from "./Art.svelte";
import EmptyArt from "./EmptyArt.svelte";
import LoaderArt from "./LoaderArt.svelte";
import "./scythe.css";

/** Scythe's module: engraved frames with corner ticks, crescents and blades, a moonlit meadow. */
const scythe: ThemeModule = { Art, EmptyArt, LoaderArt };

export default scythe;

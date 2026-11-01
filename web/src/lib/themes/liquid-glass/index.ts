import type { ThemeModule } from "../contract";
import Art from "./Art.svelte";
import EmptyArt from "./EmptyArt.svelte";
import LoaderArt from "./LoaderArt.svelte";
import "./liquid-glass.css";

/** Liquid Glass's module: clear lit glass over a colourless light field, an orb and a ring. */
const liquidGlass: ThemeModule = { Art, EmptyArt, LoaderArt };

export default liquidGlass;

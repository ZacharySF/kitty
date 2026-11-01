import type { ThemeModule } from "../contract";
import Art from "./Art.svelte";
import EmptyArt from "./EmptyArt.svelte";
import LoaderArt from "./LoaderArt.svelte";
import "./kitty.css";

/** Kitty's motif module (design Q10): sakura glass, blossom marks, the pink sleeping cat. */
const kitty: ThemeModule = { Art, EmptyArt, LoaderArt };

export default kitty;

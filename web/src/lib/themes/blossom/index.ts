import type { ThemeModule } from "../contract";
import Art from "./Art.svelte";
import EmptyArt from "./EmptyArt.svelte";
import LoaderArt from "./LoaderArt.svelte";
import "./blossom.css";

/** Blossom's motif module (design Q10, the former default): sakura glass, blossom marks, the pink sleeping cat. */
const blossom: ThemeModule = { Art, EmptyArt, LoaderArt };

export default blossom;

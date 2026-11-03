import type { ThemeModule } from "../contract";
import Art from "./Art.svelte";
import EmptyArt from "./EmptyArt.svelte";
import LoaderArt from "./LoaderArt.svelte";
import "./pretti.css";

/** pretti's module: glossy graffiti glass, heart and star bullets, heart ticks, candy dividers,
 *  a spiral loader and an airbrushed ground of targets, hearts, stars and butterflies. */
const pretti: ThemeModule = { Art, EmptyArt, LoaderArt };

export default pretti;

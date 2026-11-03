import type { ThemeModule } from "../contract";
import Art from "./Art.svelte";
import EmptyArt from "./EmptyArt.svelte";
import Frame from "./Frame.svelte";
import LoaderArt from "./LoaderArt.svelte";
import "./red.css";

/** red's module: a black block in a paper margin, one dripping blood-red brush. */
const red: ThemeModule = { Art, EmptyArt, LoaderArt, Frame };

export default red;

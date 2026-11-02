import type { ThemeModule } from "../contract";
import Art from "./Art.svelte";
import EmptyArt from "./EmptyArt.svelte";
import Frame from "./Frame.svelte";
import LoaderArt from "./LoaderArt.svelte";
import "./halfblood.css";

/** half blood's module: flat heraldic panels edged in bone over an ultramarine cloud field,
 *  a bone filigree window frame, sun bullets, red drip titles and rules, a sun-eye loader. */
const halfblood: ThemeModule = { Art, EmptyArt, LoaderArt, Frame };

export default halfblood;

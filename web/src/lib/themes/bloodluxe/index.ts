import type { ThemeModule } from "../contract";
import Art from "./Art.svelte";
import EmptyArt from "./EmptyArt.svelte";
import Frame from "./Frame.svelte";
import LoaderArt from "./LoaderArt.svelte";
import "./bloodluxe.css";

/** BloodLuxe's module: cream luxe paper in black rims over a marbled sky and grass, a
 *  barbed-wire window frame, blood-drop bullets, dripping red titles, rules and focus. */
const bloodluxe: ThemeModule = { Art, EmptyArt, LoaderArt, Frame };

export default bloodluxe;

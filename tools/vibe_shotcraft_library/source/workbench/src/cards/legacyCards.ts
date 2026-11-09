import type { CardDef } from "./types";
import { LEGACY_DEMO_MODULES } from "./legacy-index";

/** Compatibility-only definitions: keep historical IDs, names and source
 * durations without substituting a different animation or changing a project. */
export const LEGACY_CARDS: CardDef[] = LEGACY_DEMO_MODULES.map((m) => ({
  id: `demo:${m.stem}`,
  name: m.name,
  nameEn: m.nameEn,
  category: m.category,
  durationInFrames: m.duration,
  component: m.component,
  schema: [],
  accent: "#c58a2a",
}));

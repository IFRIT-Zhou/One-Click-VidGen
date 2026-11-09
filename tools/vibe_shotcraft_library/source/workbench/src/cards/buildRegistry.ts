import type { CardDef } from "./types";

/** Retired cards remain resolvable in saved projects, but are not offered for
 * new clips. A current card always wins if an ID is introduced again. */
export const buildCardRegistry = (visible: CardDef[], compatibility: CardDef[] = []) => {
  const seen = new Set<string>();
  const cardList = visible.filter((card) => {
    if (seen.has(card.id)) return false;
    seen.add(card.id);
    return true;
  });
  const cards: Record<string, CardDef> = Object.fromEntries(cardList.map((card) => [card.id, card]));
  for (const card of compatibility) {
    if (!seen.has(card.id)) {
      cards[card.id] = card;
      seen.add(card.id);
    }
  }
  return { cardList, cards };
};

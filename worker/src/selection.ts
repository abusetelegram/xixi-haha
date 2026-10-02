import { AssetValidationError } from "./errors";

const SELECTION_WHITESPACE = new Set<number>([
  0x0009, 0x000a, 0x000b, 0x000c, 0x000d, 0x0020, 0x00a0, 0x1680,
  0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007,
  0x2008, 0x2009, 0x200a, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000, 0xfeff,
]);

export function isSelectableParagraph(value: string): boolean {
  for (const character of value) {
    if (!SELECTION_WHITESPACE.has(character.codePointAt(0)!)) return true;
  }
  return false;
}

export interface SelectedParagraph {
  quote: string;
  paragraphIndex: number;
}

export function paragraphAtSelectableRank(text: readonly string[], rank: number): SelectedParagraph {
  if (!Number.isSafeInteger(rank) || rank < 0) throw new RangeError("selectable rank is invalid");
  let seen = 0;
  for (let paragraphIndex = 0; paragraphIndex < text.length; paragraphIndex += 1) {
    const quote = text[paragraphIndex]!;
    if (isSelectableParagraph(quote)) {
      if (seen === rank) return { quote, paragraphIndex };
      seen += 1;
    }
  }
  throw new AssetValidationError("article does not contain the indexed selectable rank");
}

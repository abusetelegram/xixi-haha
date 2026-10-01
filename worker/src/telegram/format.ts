import type { Quote } from "../types";

export const TELEGRAM_MESSAGE_LIMIT = 4096;
const MAX_ATTRIBUTION_TITLE = 512;

function escapeHtml(value: string): string {
  return value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

function takeLogical(value: string, limit: number): string {
  return Array.from(value).slice(0, Math.max(0, limit)).join("");
}

function truncateLogical(value: string, limit: number): string {
  const characters = Array.from(value);
  if (characters.length <= limit) return value;
  if (limit <= 0) return "";
  if (limit === 1) return "…";
  return `${characters.slice(0, limit - 1).join("")}…`;
}

/** Formats Telegram HTML while applying the 4096-character limit to visible Unicode code points. */
export function formatQuoteHtml(quote: Quote): string {
  const titleFallback = `文章 ${quote.article.id}`;
  const title = takeLogical(quote.article.title || titleFallback, MAX_ATTRIBUTION_TITLE);
  const attributionPrefix = "\n\n来源：";
  const attributionVisibleLength = Array.from(attributionPrefix).length + Array.from(title).length;
  const quoteBudget = TELEGRAM_MESSAGE_LIMIT - attributionVisibleLength;
  const displayedQuote = truncateLogical(quote.quote, quoteBudget);
  return `${escapeHtml(displayedQuote)}${attributionPrefix}<a href="${escapeHtml(quote.sourceUrl)}">${escapeHtml(title)}</a>`;
}

export function stableInlineResultId(quote: Quote): string {
  return `${quote.corpus.dataSha.slice(0, 20)}:${quote.article.id}:${quote.paragraphIndex}`.slice(0, 64);
}

export function logicalHtmlTextLengthForTest(value: string): number {
  const visible = value.replace(/<a href="[^"]*">([^<]*)<\/a>/gu, "$1").replaceAll("&quot;", '"').replaceAll("&gt;", ">").replaceAll("&lt;", "<").replaceAll("&amp;", "&");
  return Array.from(visible).length;
}

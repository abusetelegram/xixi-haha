export interface MediaItem {
  readonly type: string;
  readonly url: string;
  readonly alt: string;
}

export interface Article {
  readonly id: string;
  readonly title: string;
  readonly date: string;
  readonly author: string;
  readonly editor: string;
  readonly text: readonly string[];
  readonly content_type?: string;
  readonly media?: readonly MediaItem[];
}

export type ArticleMetadata = Pick<Article, "id" | "title" | "date" | "author" | "editor">;

export interface CorpusProvenance {
  sourceSha: string;
  dataSha: string;
}

export type SelectionMode = "paragraph" | "article";

export interface Quote {
  quote: string;
  paragraphIndex: number;
  article: ArticleMetadata;
  sourceUrl: string;
  selection: SelectionMode;
  corpus: CorpusProvenance;
}

export interface MediaItem {
  type: string;
  url: string;
  alt: string;
}

export interface Article {
  id: string;
  title: string;
  date: string;
  author: string;
  editor: string;
  text: string[];
  content_type?: string;
  media?: MediaItem[];
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

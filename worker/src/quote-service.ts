import { AssetValidationError, EmptyCorpusError } from "./errors";
import { cryptoRandomSource, locateArticleRank, locateGlobalRank, randomInteger, selectableEntryIndexes, type RandomSource } from "./index";
import { paragraphAtSelectableRank } from "./selection";
import type { AssetRepository } from "./asset-repository";
import type { Quote, SelectionMode } from "./types";

export class QuoteService {
  constructor(private readonly repository: AssetRepository, private readonly random: RandomSource = cryptoRandomSource) {}

  async randomQuote(selection: SelectionMode = "paragraph"): Promise<Quote> {
    const index = await this.repository.paragraphIndex();
    if (index.selectableParagraphCount === 0) throw new EmptyCorpusError("corpus has no selectable paragraphs");

    let selected;
    if (selection === "paragraph") {
      selected = locateGlobalRank(index, randomInteger(index.selectableParagraphCount, this.random));
    } else if (selection === "article") {
      const selectable = selectableEntryIndexes(index);
      if (selectable.length === 0) throw new EmptyCorpusError("corpus has no selectable articles");
      const entryIndex = selectable[randomInteger(selectable.length, this.random)]!;
      const previousOffset = entryIndex === 0 ? 0 : index.entries[entryIndex - 1]!.cumulativeOffset;
      const width = index.entries[entryIndex]!.cumulativeOffset - previousOffset;
      selected = locateArticleRank(index, entryIndex, randomInteger(width, this.random));
    } else {
      throw new RangeError("unsupported quote selection mode");
    }

    const article = await this.repository.getArticle(selected.articleId);
    if (article === undefined) throw new AssetValidationError("indexed article is missing from its shard");
    const paragraph = paragraphAtSelectableRank(article.text, selected.localRank);
    const corpus = await this.repository.provenance();
    return {
      ...paragraph,
      article: {
        id: article.id,
        title: article.title,
        date: article.date,
        author: article.author,
        editor: article.editor,
      },
      sourceUrl: `http://jhsjk.people.cn/article/${article.id}`,
      selection,
      corpus,
    };
  }
}

export class AssetValidationError extends Error {
  override readonly name = "AssetValidationError";
}

export class EmptyCorpusError extends Error {
  override readonly name = "EmptyCorpusError";
}

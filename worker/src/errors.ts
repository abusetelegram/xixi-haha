export class AssetValidationError extends Error {
  override readonly name = "AssetValidationError";
}

export class EmptyCorpusError extends Error {
  override readonly name = "EmptyCorpusError";
}

/** Retryable overload: adapters should return HTTP 503 without starting a fetch. */
export class AssetLoadCapacityError extends Error {
  override readonly name = "AssetLoadCapacityError";
  readonly retryable = true;
}

/**
 * Persisted river and daily-strategy signals were already validated against the
 * official primary-group map before they were written. During a deployment, a
 * fresh isolate can briefly have no primary-group entry available. Treat that
 * as unavailable verification data, not as proof that every stored signal is
 * invalid. A present but conflicting primary group must still be rejected.
 */
export function matchesPersistedOfficialPrimaryGroup(
  officialPrimaryGroup: string | undefined,
  storedSignalGroup: string | undefined,
) {
  return !officialPrimaryGroup || officialPrimaryGroup === storedSignalGroup;
}

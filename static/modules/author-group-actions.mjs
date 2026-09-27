// Shared target selection for controls on a combined author catalog.
export function authorMembers(author) {
  const members = author?.author_group_members || author?.author?.author_group_members;
  const candidates = members?.length ? members : [author?.author || author];
  return [...new Map(candidates.filter((member) => member?.asin && member?.name)
    .map((member) => [member.asin, member])).values()];
}

export async function updateAuthorReleases(api, details, payload) {
  for (const member of authorMembers(details)) {
    await api("/api/author-series", {
      method: "POST",
      body: JSON.stringify({
        ...payload,
        author_asin: member.asin,
        author_name: member.name,
      }),
    });
  }
}

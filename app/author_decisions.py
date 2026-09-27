"""Explicit identity decisions, scoped to a user's catalogue and marketplace."""

from itertools import combinations, product


DECISION_SCHEMA = """
CREATE TABLE IF NOT EXISTS author_identity_decisions (
    marketplace TEXT NOT NULL,
    left_asin TEXT NOT NULL,
    right_asin TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('merge', 'separate')),
    PRIMARY KEY (marketplace, left_asin, right_asin),
    CHECK (left_asin < right_asin)
);
CREATE TABLE IF NOT EXISTS author_search_evidence (
    marketplace TEXT NOT NULL,
    asin TEXT NOT NULL,
    book_asins TEXT NOT NULL,
    series TEXT NOT NULL,
    PRIMARY KEY (marketplace, asin)
);
"""


def decision_key(marketplace, left_asin, right_asin):
    return (marketplace, *sorted((left_asin, right_asin)))


def read_decisions(connection):
    return {
        (row['marketplace'], row['left_asin'], row['right_asin']): row['decision']
        for row in connection.execute('SELECT * FROM author_identity_decisions')
    }


def separation_map(decisions):
    result = {}
    for (marketplace, left, right), decision in decisions.items():
        if decision == 'separate':
            result.setdefault((marketplace, left), set()).add(right)
            result.setdefault((marketplace, right), set()).add(left)
    return result


def record_decision(connection, marketplace, left_asins, right_asins, decision):
    # Confirming a merge establishes one identity for all members. Separating
    # groups blocks every cross-group pair so an alias cannot bridge them later.
    pairs = (combinations(sorted(set(left_asins) | set(right_asins)), 2)
             if decision == 'merge' else product(left_asins, right_asins))
    connection.executemany(
        """INSERT INTO author_identity_decisions VALUES (?, ?, ?, ?)
        ON CONFLICT(marketplace, left_asin, right_asin)
        DO UPDATE SET decision=excluded.decision""",
        ((*decision_key(marketplace, left, right), decision) for left, right in pairs if left != right),
    )

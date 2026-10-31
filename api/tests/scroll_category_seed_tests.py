"""The built-in scroll categories are seeded at boot, by main._run_startup_seeds().

The schema is Alembic's (the 20261031 baseline is schema only); these rows are
data that every deploy refreshes, so they are seeded on boot. History: before
the baseline, production never ran Alembic, the rows lived only in Alembic
revisions, and bttco_scroll_categories shipped with zero rows, so every
/scrolls/{category_slug} lookup raised 404 and the whole feature was dead.

The seed carries SEVEN rows, not the three the migrations know about.
ScrollsPanel.tsx replaces its BUILTIN_CATEGORIES fallback wholesale the moment
the API returns anything non-empty, so a three-row seed would silently delete
the Waivers, Upkeep and Quest Log tabs. That is the drift
test_seed_carries_every_frontend_builtin_category exists to catch: it parses the
frontend constant and requires the seed to carry every entry, so adding a tab
there without adding it here fails the build instead of shipping a missing tab.

The relationship is a superset, not equality: six rows mirror BUILTIN_CATEGORIES,
and the seventh ('owner-archive') is backend-owned and deliberately not a
frontend fallback tab. test_seed_carries_no_undeclared_extra_categories pins the
extras to exactly BACKEND_OWNED_SLUGS, so the superset relaxation cannot let an
accidental row through.

These cover the seed without a database (the Tests CI job has no Postgres
service; the Schema boot job asserts the live rows).
"""

import inspect
import re
import unittest
from pathlib import Path

import main
from app.core.schema_compare import BASELINE_SQL_PATH
from app.models.scroll_category import ScrollCategory

SCROLLS_PANEL = Path(__file__).resolve().parents[2] / "app" / "src" / "components" / "ScrollsPanel.tsx"

# The reconciled state of the built-in tabs: the three the migrations define
# (20260330 seeds artist/shop/misc, 20260426 renames misc -> templates) plus the
# three that only ever existed in the frontend fallback, plus the backend-owned
# owner archive.
# (slug, label, icon, sort_order)
EXPECTED_CATEGORIES = [
    ("artist", "Artist", "📜", 0),
    ("shop", "Shop", "🏪", 1),
    ("templates", "Templates", "message-square", 2),
    ("waivers", "Waivers", "📋", 3),
    ("upkeep", "Upkeep", "🔧", 4),
    ("quests", "Quest Log", "quest", 5),
    ("owner-archive", "Artist Scrolls", "archive", 6),
]

# Categories the seed owns that ScrollsPanel's BUILTIN_CATEGORIES deliberately
# does not list. The frontend constant is a *fallback tab list*; these are rows
# the backend needs to exist for its own endpoints to resolve. 'owner-archive'
# is the owner's copy of every submitted Artist Scroll: its slug is hardcoded in
# app/services/scroll_archive.py, so without the row list_category_docs 404s
# while submit_scroll keeps filing archive rows into it.
#
# This set is what keeps the relaxed frontend check honest. The seed is allowed
# to be a superset of BUILTIN_CATEGORIES, but only by exactly these slugs -- an
# accidental extra row still fails the build.
BACKEND_OWNED_SLUGS = {"owner-archive"}

# One seeded VALUES row: (gen_random_uuid(), slug, label, icon, description, is_builtin, sort_order, ai_digest_enabled, now())
_SEED_ROW = re.compile(
    r"\(gen_random_uuid\(\),\s*"
    r"'([^']*)',\s*'([^']*)',\s*'([^']*)',\s*'([^']*)',\s*"
    r"(TRUE|FALSE),\s*(\d+),\s*(TRUE|FALSE),\s*now\(\)\)"
)

# One BUILTIN_CATEGORIES entry in ScrollsPanel.tsx.
_FRONTEND_ROW = re.compile(r"slug:\s*'([^']*)'.*?label:\s*'([^']*)'.*?icon:\s*'([^']*)'.*?sort_order:\s*(\d+)")


def _seed_statement() -> str:
    """The INSERT INTO bttco_scroll_categories statement inside the boot path."""
    source = inspect.getsource(main._run_startup_seeds)
    match = re.search(r"INSERT INTO bttco_scroll_categories.*?(?=\"\"\")", source, re.DOTALL)
    assert match, "no bttco_scroll_categories seed found in _run_startup_seeds"
    return match.group(0)


def _seeded_columns() -> list[str]:
    """Column names the seed INSERT supplies."""
    match = re.search(r"INSERT INTO bttco_scroll_categories\s*\(([^)]*)\)", _seed_statement())
    assert match, "seed INSERT does not name its columns"
    return [name.strip() for name in match.group(1).split(",")]


def _seeded_rows() -> list[tuple[str, str, str, str, str, int, str]]:
    """Parsed VALUES rows: (slug, label, icon, description, is_builtin, sort_order, ai_digest_enabled)."""
    return [
        (slug, label, icon, description, builtin, int(order), digest)
        for slug, label, icon, description, builtin, order, digest in _SEED_ROW.findall(_seed_statement())
    ]


def _frontend_builtin_categories() -> list[tuple[str, str, str, int]]:
    """BUILTIN_CATEGORIES from ScrollsPanel.tsx: (slug, label, icon, sort_order)."""
    assert SCROLLS_PANEL.is_file(), (
        f"{SCROLLS_PANEL} not found. If ScrollsPanel.tsx moved, update this path: "
        "the seed has to stay in step with the frontend's built-in tab list."
    )
    source = SCROLLS_PANEL.read_text(encoding="utf-8")
    block = re.search(r"const BUILTIN_CATEGORIES[^=]*=\s*\[(.*?)\n\]", source, re.DOTALL)
    assert block, "BUILTIN_CATEGORIES array not found in ScrollsPanel.tsx"
    return [(slug, label, icon, int(order)) for slug, label, icon, order in _FRONTEND_ROW.findall(block.group(1))]


class ScrollCategorySeedTests(unittest.TestCase):
    def test_boot_path_seeds_the_categories(self):
        """The seed lives in the boot path, which is the only thing production runs."""
        self.assertIn("INSERT INTO bttco_scroll_categories", inspect.getsource(main._run_startup_seeds))

    def test_seeds_every_builtin_category(self):
        got = [(slug, label, icon, order) for slug, label, icon, _, _, order, _ in _seeded_rows()]
        self.assertEqual(got, EXPECTED_CATEGORIES)

    def test_seed_carries_every_frontend_builtin_category(self):
        """ScrollsPanel replaces its fallback wholesale, so a short seed removes tabs.

        .then(cats => { if (cats.length > 0) setCategories(cats) })

        Any tab in BUILTIN_CATEGORIES that the seed does not carry disappears
        from the UI the moment the seed lands. That is the real invariant, and
        it is directional: the seed must be a *superset* of the frontend
        constant, matching it exactly on every row they share.

        Equality is the wrong assertion, because the seed legitimately owns rows
        the frontend fallback does not list -- see BACKEND_OWNED_SLUGS. The
        extras are pinned separately by
        test_seed_carries_no_undeclared_extra_categories, so relaxing equality
        to a superset does not let an accidental row through.
        """
        frontend = _frontend_builtin_categories()
        seeded = [(slug, label, icon, order) for slug, label, icon, _, _, order, _ in _seeded_rows()]
        seeded_by_slug = {slug: row for row in seeded for slug in (row[0],)}

        missing = [row for row in frontend if row[0] not in seeded_by_slug]
        self.assertEqual(
            missing,
            [],
            f"the seed does not carry these built-in tabs: {missing}. "
            "They would vanish from the Scrolls panel as soon as the seed lands.",
        )

        mismatched = [(row, seeded_by_slug[row[0]]) for row in frontend if seeded_by_slug[row[0]] != row]
        self.assertEqual(
            mismatched,
            [],
            f"seed and ScrollsPanel.tsx BUILTIN_CATEGORIES disagree on slug/label/icon/sort_order for: {mismatched}",
        )

    def test_seed_carries_no_undeclared_extra_categories(self):
        """Rows beyond the frontend constant must be declared backend-owned.

        Without this, the superset check above would silently accept any extra
        row someone added to the seed by mistake.
        """
        frontend_slugs = {slug for slug, *_ in _frontend_builtin_categories()}
        seeded_slugs = {slug for slug, *_ in _seeded_rows()}
        extras = seeded_slugs - frontend_slugs
        self.assertEqual(
            extras,
            BACKEND_OWNED_SLUGS,
            f"seed carries categories that are neither in BUILTIN_CATEGORIES nor declared "
            f"backend-owned: {sorted(extras - BACKEND_OWNED_SLUGS)}. "
            f"Declared-but-unseeded: {sorted(BACKEND_OWNED_SLUGS - extras)}.",
        )

    def test_sort_orders_and_slugs_are_unique(self):
        """Duplicate sort_order makes tab order non-deterministic; duplicate slug
        would be swallowed by ON CONFLICT and silently drop a category."""
        rows = _seeded_rows()
        orders = [order for _, _, _, _, _, order, _ in rows]
        slugs = [slug for slug, *_ in rows]
        self.assertEqual(len(set(orders)), len(orders), f"duplicate sort_order in the seed: {sorted(orders)}")
        self.assertEqual(len(set(slugs)), len(slugs), f"duplicate slug in the seed: {sorted(slugs)}")
        self.assertEqual(
            orders,
            list(range(len(orders))),
            f"sort_order must be contiguous from 0 so tabs render in a stable order, got {orders}",
        )

    def test_reconciles_the_superseded_misc_tab(self):
        """20260426 renamed misc to templates. Boot must repair legacy state."""
        statement = _seed_statement()
        startup = inspect.getsource(main._run_startup_seeds)
        self.assertIn("WHERE slug = 'misc'", startup)
        self.assertIn("SET slug = 'templates'", startup)
        self.assertNotIn("'misc',", statement)
        self.assertNotIn("'Misc'", statement)

    def test_every_seeded_category_is_builtin(self):
        """DELETE /scrolls/categories/{slug} refuses builtins; none of these seven is deletable.

        create_category always writes is_builtin=False, and _slugify('Quest Log')
        is 'quest_log', so a deleted builtin could not be recreated with the slug
        its hardcoded render branch matches on.

        'owner-archive' needs the flag for a stronger reason than a missing tab:
        its slug is hardcoded in app/services/scroll_archive.py, so deleting the
        row makes list_category_docs 404 while submit_scroll keeps filing archive
        rows into it -- the owner silently loses access to compliance records
        that are still accumulating. _slugify('Artist Scrolls') is
        'artist_scrolls', so it could not be recreated with the right slug either.
        """
        for slug, _, _, _, is_builtin, _, _ in _seeded_rows():
            with self.subTest(slug=slug):
                self.assertEqual(is_builtin, "TRUE")

    def test_seed_is_idempotent_and_repairs_reserved_slug_drift(self):
        """Every boot restores the canonical fields for all reserved slugs."""
        statement = _seed_statement()
        self.assertIn("ON CONFLICT (slug) DO UPDATE SET", statement)
        for assignment in (
            "label = EXCLUDED.label",
            "icon = EXCLUDED.icon",
            "description = EXCLUDED.description",
            "is_builtin = TRUE",
            "sort_order = EXCLUDED.sort_order",
            "ai_digest_enabled = FALSE",
        ):
            with self.subTest(assignment=assignment):
                self.assertIn(assignment, statement)

    def test_slug_conflict_target_is_in_the_baseline_schema(self):
        """ON CONFLICT (slug) needs the unique slug index; the Alembic baseline creates it."""
        baseline = BASELINE_SQL_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "CREATE UNIQUE INDEX ux_bttco_scroll_categories_slug ON public.bttco_scroll_categories USING btree (slug);",
            baseline,
        )

    def test_boot_log_counts_only_builtin_categories(self):
        startup = inspect.getsource(main._run_startup_seeds)
        self.assertIn(
            "SELECT count(*) FROM bttco_scroll_categories WHERE is_builtin = TRUE",
            startup,
        )
        self.assertNotIn(
            'fetchval("SELECT count(*) FROM bttco_scroll_categories")',
            startup,
        )

    def test_supplies_every_column_the_database_has_no_server_default_for(self):
        """create_all gives Python-side defaults no server default; the seed must fill them.

        This is the bug PR #181 fixed for bttco_quests. Omitting one of these
        makes the INSERT raise NotNullViolation, which the shared non-fatal
        handler swallows -- taking every later statement with it.
        """
        seeded = set(_seeded_columns())
        required = {
            column.name
            for column in ScrollCategory.__table__.columns
            if not column.nullable and column.server_default is None
        }
        self.assertIn("id", required, "guard is not exercising the id column")
        self.assertIn("created_at", required, "guard is not exercising the created_at column")
        missing = sorted(required - seeded)
        self.assertEqual(
            missing,
            [],
            f"seed omits NOT NULL columns with no server default: {missing}. "
            "The INSERT would raise NotNullViolation and be swallowed on boot.",
        )

    def test_generates_ids_server_side(self):
        """ScrollCategory.id has only a Python-side default, so raw SQL must generate one."""
        self.assertEqual(len(_SEED_ROW.findall(_seed_statement())), len(EXPECTED_CATEGORIES))

    def test_seeds_issue_no_schema_ddl(self):
        """Startup owns data only; every schema change is an Alembic revision."""
        source = inspect.getsource(main._run_startup_seeds)
        for ddl in ("CREATE TABLE", "ALTER TABLE", "CREATE INDEX", "CREATE UNIQUE INDEX", "DROP TABLE", "create_all("):
            with self.subTest(statement=ddl):
                self.assertNotIn(ddl, source)


if __name__ == "__main__":
    unittest.main()

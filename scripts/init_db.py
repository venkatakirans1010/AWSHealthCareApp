"""Create or update the claims tables in the database at DATABASE_URL.

Applies src/database/schema.sql, then every
src/database/migrations/*.sql file in name order. These SQL files only create
or add missing objects (IF NOT EXISTS) and never drop tables or delete rows, so
running this again against a database that already holds claims is safe. Works
for local PostgreSQL and Amazon RDS.

Usage, from the project root:
    python scripts/init_db.py                                # uses DATABASE_URL
    python scripts/init_db.py --url-env TEST_DATABASE_URL    # uses another environment variable
"""

import argparse
import os
import sys
from pathlib import Path

import psycopg
from sqlalchemy.engine import make_url


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import app.config  # noqa: E402,F401  (loads the project-root .env file)


DATABASE_DIR = PROJECT_ROOT / "src" / "database"
SCHEMA_FILE = DATABASE_DIR / "schema.sql"
MIGRATIONS_DIR = DATABASE_DIR / "migrations"


def sql_files(
    schema_file: Path = SCHEMA_FILE,
    migrations_dir: Path = MIGRATIONS_DIR,
) -> list[Path]:
    """Return the base schema followed by migrations in filename order."""
    return [schema_file, *sorted(migrations_dir.glob("*.sql"))]


def apply_schema(
    database_url: str,
    schema_file: Path = SCHEMA_FILE,
    migrations_dir: Path = MIGRATIONS_DIR,
) -> None:
    """Run the schema and migrations; each SQL file manages its transaction."""
    conninfo = (
        make_url(database_url)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )
    with psycopg.connect(conninfo, autocommit=True, connect_timeout=10) as connection:
        for sql_file in sql_files(schema_file, migrations_dir):
            connection.execute(sql_file.read_text(encoding="utf-8"))  # type: ignore[arg-type]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--url-env",
        default="DATABASE_URL",
        help="Environment variable holding the database URL.",
    )
    args = parser.parse_args()

    database_url = os.getenv(args.url_env, "").strip()
    if not database_url:
        print(f"{args.url_env} is not set.", file=sys.stderr)
        return 1

    target = make_url(database_url).render_as_string(hide_password=True)
    print(f"Applying {', '.join(path.name for path in sql_files())} to {target} ...")
    apply_schema(database_url)
    print("Done. Existing tables and data were left unchanged.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""One-time copy from a CURRENT DairyAI SQLite v0.2 schema to empty PostgreSQL.

Usage:
  python -m scripts.copy_sqlite_to_postgres --sqlite ./dairyai.db --dry-run
  DATABASE_URL='postgresql://...' python -m scripts.copy_sqlite_to_postgres --sqlite ./dairyai.db --execute

Before execution back up BOTH databases. Never run against a nonempty destination.
This is a guarded data-copy, not a conversion tool for obsolete pre-tenant schemas.
"""
import argparse
import os
from pathlib import Path
from sqlalchemy import create_engine, inspect, select, text
from app.database import Base
from app import models  # noqa


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sqlite',required=True,help='Path to existing SQLite database')
    parser.add_argument('--execute',action='store_true',help='Copy to PostgreSQL; dry-run by default')
    args=parser.parse_args()
    path=Path(args.sqlite).resolve()
    if not path.is_file():parser.error(f'SQLite file not found: {path}')
    source=create_engine('sqlite:///'+str(path))
    url=os.environ.get('DATABASE_URL','')
    if not url.startswith(('postgresql://','postgresql+psycopg://','postgres://')):
        parser.error('DATABASE_URL must be a PostgreSQL connection URL')
    url=url.replace('postgres://','postgresql+psycopg://',1).replace('postgresql://','postgresql+psycopg://',1)
    dest=create_engine(url,pool_pre_ping=True)
    src_tables=set(inspect(source).get_table_names());dst_tables=set(inspect(dest).get_table_names())
    desired={table.name for table in Base.metadata.sorted_tables}
    if not desired.issubset(dst_tables):parser.error('Destination schema is incomplete: run alembic upgrade head first')
    missing=desired-src_tables
    if missing:parser.error('Source SQLite is missing current tables: '+', '.join(sorted(missing))+'; migrate old SQLite schema first')
    counts={}
    with source.connect() as s, dest.connect() as d:
        for table in Base.metadata.sorted_tables:
            source_cols={c['name'] for c in inspect(source).get_columns(table.name)}
            required={c.name for c in table.columns if not c.nullable and c.default is None and c.server_default is None and not c.primary_key}
            if required-source_cols:parser.error(f'{table.name} missing required columns: {required-source_cols}')
            source_count=s.execute(text(f'SELECT COUNT(*) FROM "{table.name}"')).scalar_one()
            dest_count=d.execute(text(f'SELECT COUNT(*) FROM "{table.name}"')).scalar_one()
            if dest_count:parser.error(f'Destination {table.name} already contains {dest_count} rows; refusing to overwrite')
            counts[table.name]=source_count
    print('Source rows:',counts)
    if not args.execute:
        print('DRY RUN ONLY. Re-run with --execute after a verified backup.')
        return
    with source.connect() as s, dest.begin() as d:
        for table in Base.metadata.sorted_tables:
            src_cols={c['name'] for c in inspect(source).get_columns(table.name)}
            columns=[c for c in table.columns if c.name in src_cols]
            rows=s.execute(select(*columns))
            copied=0
            while True:
                chunk=rows.fetchmany(250)
                if not chunk:break
                d.execute(table.insert(),[dict(r._mapping) for r in chunk])
                copied+=len(chunk)
            print(f'{table.name}: {copied}')
        for table in Base.metadata.sorted_tables:
            if 'id' in table.c:
                d.exec_driver_sql(f'SELECT setval(pg_get_serial_sequence(\'{table.name}\', \'id\'), '
                                  f'GREATEST(COALESCE((SELECT MAX(id) FROM "{table.name}"), 1),1), '
                                  f'(SELECT COUNT(*)>0 FROM "{table.name}"))')
    print('Data copied. Compare row counts and validate on a staging app before switching production.')

if __name__=='__main__':main()

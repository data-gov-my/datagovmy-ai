"""Prepare one environment's database on the shared RDS instance.

Creates the database (prod uses the instance's initial `dgmyai`), the
pgvector extension, and two users:

- `dgmyai_<env>_ingest`: owns the tables (created by ingest on first run)
- `dgmyai_<env>_app`: read-only, used by the API

Each user's connection string is written to SSM as a SecureString
(`DATABASE_URL` for the API, `DATABASE_URL_INGEST` for ingest), so no person
ever handles the passwords. Safe to re-run: existing users and parameters are
kept unless ROTATE=true.

Runs as a one-off ECS task with the RDS admin secret (see infra/README.md).
"""

import json
import os
import secrets

import psycopg
from psycopg import sql

DB_PORT_DEFAULT = "5432"


def database_name(env: str) -> str:
    return "dgmyai" if env == "prod" else f"dgmyai_{env}"


def connection_url(user, password, host, port, db, sslmode="require") -> str:
    # token_urlsafe passwords need no URL escaping
    return (
        f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}?sslmode={sslmode}"
    )


def bootstrap(
    env,
    host,
    port,
    admin_user,
    admin_password,
    ssm,
    param_prefix,
    rotate=False,
    sslmode="require",
):
    db = database_name(env)
    users = {
        "ingest": (f"dgmyai_{env}_ingest", f"{param_prefix}/DATABASE_URL_INGEST"),
        "app": (f"dgmyai_{env}_app", f"{param_prefix}/DATABASE_URL"),
    }

    def connect(dbname):
        return psycopg.connect(
            host=host,
            port=port,
            user=admin_user,
            password=admin_password,
            dbname=dbname,
            sslmode=sslmode,
            autocommit=True,
        )

    def param_exists(name):
        try:
            ssm.get_parameter(Name=name, WithDecryption=False)
            return True
        except ssm.exceptions.ParameterNotFound:
            return False

    with connect("postgres") as conn:
        if not conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (db,)
        ).fetchone():
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db)))
            print(f"created database {db}")

        for user, param in users.values():
            exists = conn.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s", (user,)
            ).fetchone()
            if exists and not rotate and param_exists(param):
                print(f"kept user {user}")
                continue
            password = secrets.token_urlsafe(32)
            verb = "ALTER" if exists else "CREATE"
            conn.execute(
                sql.SQL(verb + " ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(user), sql.Literal(password)
                )
            )
            ssm.put_parameter(
                Name=param,
                Value=connection_url(user, password, host, port, db, sslmode),
                Type="SecureString",
                Overwrite=True,
            )
            print(f"{'updated' if exists else 'created'} user {user}, wrote {param}")

    ingest, app = users["ingest"][0], users["app"][0]
    with connect(db) as conn:
        q = sql.SQL
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        for stmt in [
            # The admin isn't a superuser on RDS: membership in the ingest role
            # lets it grant on ingest's tables and set its default privileges
            q("GRANT {} TO CURRENT_USER").format(sql.Identifier(ingest)),
            # Only this environment's users may connect to its database
            q("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(db)),
            q("GRANT CONNECT ON DATABASE {} TO {}, {}").format(
                sql.Identifier(db), sql.Identifier(ingest), sql.Identifier(app)
            ),
            q("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(
                sql.Identifier(ingest)
            ),
            q("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(app)),
            q("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(
                sql.Identifier(app)
            ),
            # Tables ingest creates later are readable by the API
            q(
                "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
                "GRANT SELECT ON TABLES TO {}"
            ).format(sql.Identifier(ingest), sql.Identifier(app)),
        ]:
            conn.execute(stmt)
    print(f"database {db} ready")


def main():
    import boto3

    env = os.environ["ENVIRONMENT"]
    admin = json.loads(os.environ["DB_ADMIN_SECRET"])  # injected from Secrets Manager
    bootstrap(
        env=env,
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", DB_PORT_DEFAULT),
        admin_user=admin["username"],
        admin_password=admin["password"],
        ssm=boto3.client("ssm"),
        param_prefix=os.environ["PARAM_PREFIX"],
        rotate=os.environ.get("ROTATE", "").lower() == "true",
    )


if __name__ == "__main__":
    main()

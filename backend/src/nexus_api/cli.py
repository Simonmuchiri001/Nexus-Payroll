"""Command-line entry points for operations that must not be exposed over HTTP."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

from nexus_api.bootstrap import BootstrapRequest, bootstrap_tenant
from nexus_api.database.session import SessionFactory, engine
from nexus_api.identity.service import seed_identity


async def _bootstrap(args: argparse.Namespace) -> None:
    password = args.admin_password or os.environ.get("NEXUS_BOOTSTRAP_ADMIN_PASSWORD")
    if not password:
        password = getpass.getpass("Admin password: ")
    request = BootstrapRequest(
        organization_slug=args.organization_slug,
        organization_name=args.organization_name,
        company_code=args.company_code,
        company_name=args.company_name,
        admin_email=args.admin_email,
        admin_password=password,
        admin_full_name=args.admin_name,
        country_code=args.country_code,
        base_currency=args.currency,
    )
    async with SessionFactory() as session, session.begin():
        organization = await bootstrap_tenant(session, request)
    await engine.dispose()
    print(  # noqa: T201 - CLI command result
        f"Bootstrapped organisation {organization.slug} ({organization.id})."
    )


async def _seed_reference() -> None:
    async with SessionFactory() as session, session.begin():
        await seed_identity(session)
    await engine.dispose()
    print("Reference permissions and roles are installed.")  # noqa: T201 - CLI command result


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="nexus-api",
        description="Nexus Enterprise operational commands",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    bootstrap = subcommands.add_parser("bootstrap", help="Create the first tenant and admin")
    bootstrap.add_argument("--organization-slug", required=True)
    bootstrap.add_argument("--organization-name", required=True)
    bootstrap.add_argument("--company-code", required=True)
    bootstrap.add_argument("--company-name", required=True)
    bootstrap.add_argument("--admin-email", required=True)
    bootstrap.add_argument("--admin-name", required=True)
    bootstrap.add_argument("--admin-password", default=None)
    bootstrap.add_argument("--country-code", default="KE")
    bootstrap.add_argument("--currency", default="KES")

    subcommands.add_parser("seed-reference", help="Install RBAC reference data")

    args = parser.parse_args()
    try:
        if args.command == "bootstrap":
            asyncio.run(_bootstrap(args))
        else:
            asyncio.run(_seed_reference())
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()

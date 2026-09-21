"""Tenant-owned business records and constrained PostgreSQL data sources.

Revision ID: 0002
Revises: 0001
"""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Unversioned local development databases may already have these tables
    # from create_all; the legacy bootstrap still runs every newer migration.
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("business_data_sources"):
        op.create_table(
            "business_data_sources",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("tenant_id", sa.String(36), nullable=False),
            sa.Column("tool_id", sa.String(36), sa.ForeignKey("tool_definitions.id"), nullable=False, unique=True),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("fields", sa.JSON(), nullable=False),
            sa.Column("filter_fields", sa.JSON(), nullable=False),
            sa.Column("required_filters", sa.JSON(), nullable=False),
            sa.Column("max_rows", sa.Integer(), nullable=False),
            sa.Column("postgres_dsn_encrypted", sa.Text(), nullable=True),
            sa.Column("postgres_schema", sa.String(63), nullable=True),
            sa.Column("postgres_table", sa.String(63), nullable=True),
            sa.Column("tenant_column", sa.String(63), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_business_data_sources_tenant_id", "business_data_sources", ["tenant_id"])
    if not inspector.has_table("business_records"):
        op.create_table(
            "business_records",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("source_id", sa.String(36), sa.ForeignKey("business_data_sources.id", ondelete="CASCADE"), nullable=False),
            sa.Column("tenant_id", sa.String(36), nullable=False),
            sa.Column("values", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_business_records_tenant_source", "business_records", ["tenant_id", "source_id"])


def downgrade() -> None:
    op.drop_index("ix_business_records_tenant_source", table_name="business_records")
    op.drop_table("business_records")
    op.drop_index("ix_business_data_sources_tenant_id", table_name="business_data_sources")
    op.drop_table("business_data_sources")

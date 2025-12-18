"""add can_message flag to users

Revision ID: 1f0b86b044de
Revises: 9f28ff2f0312
Create Date: 2025-01-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1f0b86b044de'
down_revision: Union[str, None] = '9f28ff2f0312'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tgusers', sa.Column('can_message', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    op.drop_column('tgusers', 'can_message')

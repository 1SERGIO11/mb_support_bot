"""add mirrored messages table

Revision ID: 80d0c6db4f5a
Revises: 1f0b86b044de
Create Date: 2024-07-08 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '80d0c6db4f5a'
down_revision = '1f0b86b044de'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'mirrored_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('admin_chat_id', sa.Integer(), nullable=False),
        sa.Column('admin_msg_id', sa.Integer(), nullable=False),
        sa.Column('user_chat_id', sa.Integer(), nullable=False),
        sa.Column('user_msg_id', sa.Integer(), nullable=False),
        sa.Column('thread_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('admin_chat_id', 'admin_msg_id'),
    )
    op.create_index(
        'idx_mirrors_user',
        'mirrored_messages',
        ['user_chat_id', 'user_msg_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('idx_mirrors_user', table_name='mirrored_messages')
    op.drop_table('mirrored_messages')

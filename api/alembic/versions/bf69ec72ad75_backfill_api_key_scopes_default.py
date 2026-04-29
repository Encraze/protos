"""backfill api_key scopes default

Revision ID: bf69ec72ad75
Revises: 9a571e2d501b
Create Date: 2026-04-29 10:59:02.014855

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'bf69ec72ad75'
down_revision: Union[str, None] = '9a571e2d501b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE api_keys
        SET scopes = ARRAY['chat:write']::varchar[]
        WHERE scopes = '{}'::varchar[] OR scopes IS NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE api_keys
        SET scopes = '{}'::varchar[]
        WHERE scopes = ARRAY['chat:write']::varchar[]
        """
    )

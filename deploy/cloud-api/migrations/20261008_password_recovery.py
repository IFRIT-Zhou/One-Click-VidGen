"""Run with the same DATABASE_URL as the cloud API before deploying recovery."""
from sqlalchemy import inspect,text
from app.db import engine
from app.password_recovery import PasswordRecoveryCode,PasswordRecoveryLimit

with engine.begin() as connection:
    if 'auth_version' not in {c['name'] for c in inspect(connection).get_columns('users')}:
        connection.execute(text('ALTER TABLE users ADD COLUMN auth_version INTEGER NOT NULL DEFAULT 0'))
    PasswordRecoveryCode.__table__.create(connection,checkfirst=True)
    PasswordRecoveryLimit.__table__.create(connection,checkfirst=True)
print('Password recovery schema is ready.')

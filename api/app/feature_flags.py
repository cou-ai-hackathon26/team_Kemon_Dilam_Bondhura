"""Operator and workspace-owner switches for optional request handlers."""
import argparse
from contextlib import closing

from pydantic import StrictBool
from fastapi import APIRouter, Depends
from api.app import security as sec
from api.app.contracts import Contract
from api.app.routes import db_connection, principal

router = APIRouter(prefix='/api/workspace/features')

ROUTES = {
    '/api/workspace/cash-corrections': 'cash_corrections',
    '/api/assist/voice': 'voice',
    '/api/assist/scan': 'bill_scan',
    '/api/assist/ask': 'assistant',
    '/api/assist/extract': 'assistant',
    '/api/assist/offer': 'offer_reader',
    '/api/financing/match': 'financing_match',
    '/api/copilot/agent-review': 'agent_mode',
    '/api/copilot/ask': 'ask_books',
    '/api/copilot/aliases/{kind}': 'ask_books',
    '/api/copilot/gap-fix': 'collection_plans',
    '/api/copilot/gap-fix/accept': 'collection_plans',
    '/api/copilot/message/approve': 'collection_plans',
    '/api/copilot/message': 'collection_plans',
    '/api/passport/create': 'passport_issue',
    '/api/statements/preview': 'statement_import',
    '/api/statements/confirm': 'statement_import',
    '/api/payments/share': 'payment_intake',
}
FEATURES = sorted(set(ROUTES.values()))


def schema(db):
    with db:
        db.execute("""CREATE TABLE IF NOT EXISTS feature_flags (
            name TEXT NOT NULL, scope TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN(0,1)),
            updated_at INTEGER NOT NULL, PRIMARY KEY(name,scope))""")


def workspace_status(db, business_id, path):
    name = ROUTES.get(path)
    if not name:
        return db.execute('SELECT kind,archived_at,1 FROM workspace_meta WHERE business_id=?', (business_id,)).fetchone()
    # The existing workspace lookup also checks both switches in one database request.
    return db.execute("""SELECT w.kind,w.archived_at,NOT EXISTS(
        SELECT 1 FROM feature_flags WHERE name=? AND scope IN('global',?) AND enabled=0
        ) FROM (SELECT ? AS business_id) AS b LEFT JOIN workspace_meta w ON w.business_id=b.business_id""",
        (name, 'workspace:' + business_id, business_id)).fetchone()


def set_flag(db, name, scope, enabled):
    db.execute("""INSERT INTO feature_flags(name,scope,enabled,updated_at) VALUES(?,?,?,?)
        ON CONFLICT(name,scope) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at""",
        (name, scope, int(enabled), sec.now()))


def settings(db, business_id):
    scope = 'workspace:' + business_id
    flags = {(n, s): bool(v) for n, s, v in db.execute(
        "SELECT name,scope,enabled FROM feature_flags WHERE scope IN ('global',?)", (scope,)).fetchall()}
    return [dict(name=n, operator_enabled=flags.get((n,'global'), True),
                 workspace_enabled=flags.get((n,scope), True),
                 enabled=flags.get((n,'global'), True) and flags.get((n,scope), True)) for n in FEATURES]


@router.get('')
def list_settings(actor=Depends(principal), db=Depends(db_connection)):
    if actor.role != 'owner':
        sec.fail(403, 'OWNER_REQUIRED')
    return {'features': settings(db, actor.business_id), 'simulation_only': True}


class FlagChange(Contract):
    enabled: StrictBool


@router.post('/{name}')
def change(name: str, body: FlagChange, actor=Depends(principal), db=Depends(db_connection)):
    with sec.transaction(db):
        current = sec.recheck(db, actor)
        if current.role != 'owner' or current.business_id != actor.business_id:
            sec.fail(403, 'OWNER_REQUIRED')
        if name not in FEATURES:
            sec.fail(404, 'UNKNOWN_FEATURE')
        set_flag(db, name, 'workspace:' + current.business_id, body.enabled)
        sec.audit(db, current, 'feature-flags', 'FEATURE_CHANGED', dict(name=name, enabled=body.enabled))
    return {'features': settings(db, actor.business_id), 'simulation_only': True}


def main():
    """Run only with the operator's existing database credentials."""
    from api.app.database import connect
    parser = argparse.ArgumentParser(description='Change a global Capzoryx feature switch.')
    parser.add_argument('feature', choices=FEATURES)
    parser.add_argument('state', choices=['on', 'off'])
    args = parser.parse_args()
    with closing(connect()) as db:
        schema(db)
        with sec.transaction(db):
            set_flag(db, args.feature, 'global', args.state == 'on')
    print(f'{args.feature} {args.state}')


if __name__ == '__main__':
    main()

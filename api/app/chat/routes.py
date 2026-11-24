"""In-app chat: the same brain the messaging bots use, for any workspace member."""
from fastapi import APIRouter, Depends
from pydantic import Field

from api.app import security as sec
from api.app.chat import brain
from api.app.contracts import Contract
from api.app.routes import db_connection, principal

router = APIRouter(prefix='/api/chat')


class Message(Contract):
    text: str = Field(min_length=1, max_length=240)


@router.post('/message')
def message(body: Message, actor=Depends(principal), db=Depends(db_connection)):
    sec.budgets(db, [('web-chat:' + actor.user_id, 30)])
    with sec.transaction(db):
        current = sec.recheck(db, actor)
        if current.role not in ('owner', 'approver', 'viewer', 'bookkeeper'):
            sec.fail(403, 'MEMBER_REQUIRED')
        ctx = brain.Context('web', sec.signature('web-chat', current.business_id + '/' + current.user_id),
                            current.business_id, current.user_id, role=current.role)
        # The app has its own reviewed draft card for entries, so the web brain only answers.
        reply = brain.decide(db, ctx, body.text)
    if reply.save is not None:
        return dict(understood=False, text='', buttons=[], simulation_only=True)
    if reply.llm is not None:
        reply = brain.finish(db, ctx, reply)
    return dict(understood=reply.understood, text=reply.text, buttons=[dict(label=l, payload=p) for l, p in reply.buttons],
                lang=reply.lang, simulation_only=True)

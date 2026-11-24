"""Business data the owner submits: sealed, private, used by the AI and, when allowed, to steer web research."""
from contextlib import closing

from api.app.chat import context
from api.app.database import connect
from api.tests.test_accounts import signup, world  # noqa: F401
from api.tests.test_market import net, setup_gold  # noqa: F401
from api.tests.test_workspaces_sharing import second_person

NOTES = '/api/business/notes'


def test_owner_submits_business_data_that_research_and_chat_use(world, net, monkeypatch):  # noqa: F811
    c, _ = world
    monkeypatch.setenv('CAPZORYX_CHAT_GEMINI_KEY', 'test-key')
    signup(c, 'data_owner')
    empty = c.get(NOTES).json()
    assert empty['notes'] == [] and 'competitors' in empty['kinds'] and not empty['profile_ready']
    # Notes work before setup too; nothing is researched without a profile and consent.
    first = c.post(NOTES, json={'kind': 'customers', 'title': 'Who buys', 'text': 'Mostly brides from Mirpur before Eid.'})
    assert first.status_code == 201 and not first.json()['research_started']
    setup_gold(c)
    c.post('/api/business/complete', json={}).raise_for_status()
    asked = len(net['gemini'])
    added = c.post(NOTES, json={'kind': 'competitors', 'title': 'New shop', 'text': 'Swarna Kutir opened near Mirpur 10 last month.',
                                'file_name': 'notes.txt'}).json()
    assert added['research_started'] and len(net['gemini']) == asked + 1
    assert 'Swarna Kutir opened near Mirpur 10' in net['gemini'][-1]  # the note steers the search
    private = c.post(NOTES, json={'kind': 'sales', 'title': 'Margins', 'text': 'Profit last month 4,20,000.', 'research': False}).json()
    assert not private['research_started'] and len(private['notes']) == 3
    assert 'Profit last month' not in net['gemini'][-1]
    business = c.get('/api/session').json()['business_id']
    with closing(connect()) as db:
        stored = db.execute('SELECT data_enc FROM biz_notes WHERE business_id=?', (business,)).fetchall()
        assert all('Swarna' not in row[0] and 'Profit' not in row[0] for row in stored)  # sealed at rest
        db.execute('BEGIN')
        facts = context.text(db, business)
        db.execute('ROLLBACK')
    assert 'Swarna Kutir' in facts and 'Profit last month 4,20,000' in facts  # the AI knows everything the owner gave
    note = private['notes'][0]['id']
    assert len(c.post(f'{NOTES}/{note}/delete', json={}).json()['notes']) == 2
    assert c.post(f'{NOTES}/{note}/delete', json={}).status_code == 404
    assert c.post(NOTES, json={'kind': 'gossip', 'title': 'x', 'text': 'y'}).status_code == 422


def test_business_data_is_private_to_the_owner(world, net):  # noqa: F811
    c, _ = world
    signup(c, 'data_private')
    c.post(NOTES, json={'kind': 'plans', 'title': 'Branch', 'text': 'Open a second branch in Uttara.'}).raise_for_status()
    second_person(c, 'data_stranger')
    assert c.get(NOTES).json()['notes'] == []

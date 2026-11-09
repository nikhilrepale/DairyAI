"""Run with: python -m pytest -q tests. All data is fictional."""
import os
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import pytest

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL',f'sqlite:///{tmp_path / "pilot.db"}')
    monkeypatch.setenv('JWT_SECRET_KEY','unit-test-secret-'+'x'*60)
    # clear modules to avoid reusing a session bound to a different database
    for key in list(sys.modules):
        if key=='app' or key.startswith('app.'):
            del sys.modules[key]
    from app.database import Base, engine
    from app import models  # noqa
    Base.metadata.create_all(engine)
    from app.main import app
    from fastapi.testclient import TestClient
    with TestClient(app) as test:
        yield test
    engine.dispose()

@pytest.fixture()
def owner(client):
    result=client.post('/api/auth/register',json={'dairy_name':'Test Dairy','dairy_code':'TEST001','name':'Demo Owner',
        'email':'owner@example.test','password':'strong-test-password-0000','preferred_language':'mr'})
    assert result.status_code==201,result.text
    return {'Authorization':'Bearer '+result.json()['access_token']}

def test_owner_auth_and_tenant_isolation(client,owner):
    c=client.post('/api/customers',json={'code':'101','name':'Farmer One'},headers=owner)
    assert c.status_code==200,c.text
    assert len(client.get('/api/customers',headers=owner).json())==1
    other=client.post('/api/auth/register',json={'dairy_name':'Other Dairy','dairy_code':'OTHER001','name':'Other Owner',
        'email':'other@example.test','password':'strong-test-password-9999'})
    assert other.status_code==201
    h2={'Authorization':'Bearer '+other.json()['access_token']}
    assert client.get('/api/customers',headers=h2).json()==[]
    assert client.get('/api/customers/'+str(c.json()['id']),headers=h2).status_code==404

def test_milk_feed_and_terms(client,owner):
    customer=client.post('/api/customers',json={'code':'D101','name':'Sample Farmer'},headers=owner).json()['id']
    day=date.today()-timedelta(days=15)
    rates=client.post('/api/rate-charts',headers=owner,json={'name':'Verified sample only','milk_type':'cow',
        'chart_type':'FAT_SNF_MATRIX','effective_from':(day-timedelta(days=2)).isoformat(),
        'match_policy':'EXACT_MATCH','entries':[{'fat_value':'4.0','snf_value':'8.5','rate':'40.00'},
          {'fat_value':'4.0','snf_value':'8.6','rate':'40.50'},
          {'fat_value':'4.1','snf_value':'8.5','rate':'41.00'},
          {'fat_value':'4.1','snf_value':'8.6','rate':'41.50'}]})
    assert rates.status_code==201,rates.text
    assert client.post(f'/api/rate-charts/{rates.json()["id"]}/activate',headers=owner).status_code==200
    saved=client.post('/api/v2/milk/manual',headers=owner,json={'customer_id':customer,'collection_date':day.isoformat(),
        'session':'morning','milk_type':'cow','quantity':'10','fat':'4.0','snf':'8.5'})
    assert saved.status_code==201,saved.text
    assert Decimal(saved.json()['amount'])==Decimal('400.00')
    products=client.post('/api/feed/products',headers=owner,json={'product_name':'Feed A','product_code':'FEED-A',
         'category':'Concentrate','unit':'BAG','purchase_price':'900','selling_price':'1000','low_stock_threshold':'2'})
    assert products.status_code==201,products.text
    pid=products.json()['id']
    import uuid
    body={'purchase_date':day.isoformat(),'idempotency_key':'purchase-'+uuid.uuid4().hex,
          'items':[{'product_id':pid,'quantity':'10','purchase_rate':'900'}]}
    purchased=client.post('/api/v2/feed/stock/bulk',headers=owner,json=body)
    assert purchased.status_code==200,purchased.text
    purchased2=client.post('/api/v2/feed/stock/bulk',headers=owner,json=body)
    assert purchased2.status_code==200
    assert purchased2.json()['purchases'][0]['created'] is False
    sale=client.post('/api/feed/sales',headers=owner,json={'customer_id':customer,'sale_date':day.isoformat(),
        'items':[{'product_id':pid,'quantity':'1'}],'idempotency_key':'sale-test-00001'})
    assert sale.status_code==201,sale.text
    assert Decimal(sale.json()['sale']['total_amount'])==Decimal('1000.00')
    policy=client.post('/api/settings/financial-policy',headers=owner,json={'effective_from':(day-timedelta(days=2)).isoformat(),
       'anamat_enabled':True,'anamat_rate':'1.00','interest_enabled':True,'annual_interest_rate':'12.0',
       'interest_method':'REDUCING_BALANCE','interest_period':'PER_BILLING_TERM','day_count_convention':'ACTUAL_365',
       'billing_term_days':10})
    assert policy.status_code==201,policy.text
    adv=client.post('/api/v2/advances',headers=owner,json={'customer_id':customer,'principal':'500',
       'installment':'100','advance_date':day.isoformat()})
    assert adv.status_code==201,adv.text
    start,end=day-timedelta(days=2),day+timedelta(days=2)
    uri=f'?start={start.isoformat()}&end={end.isoformat()}'
    preview=client.get('/api/v2/billing/preview'+uri,headers=owner)
    assert preview.status_code==200,preview.text
    bill=next(b for b in preview.json()['bills'] if b['customer_id']==customer)
    assert bill['milk_amount']=='400.00'
    assert bill['feed_amount']=='1000.00'
    assert bill['anamat_amount']=='10.00'
    assert bill['advance_principal']=='100.00'
    assert Decimal(bill['payable']) < 0
    finalized=client.post('/api/v2/billing/finalize',headers=owner,json={'start':start.isoformat(),'end':end.isoformat()})
    assert finalized.status_code==200,finalized.text
    again=client.post('/api/v2/billing/finalize',headers=owner,json={'start':start.isoformat(),'end':end.isoformat()})
    assert again.json()['already_finalized'] is True
    snapshot=client.get('/api/v2/billing/'+str(finalized.json()['period_id'])+'/bills',headers=owner)
    assert snapshot.status_code==200
    snap=snapshot.json()['bills'][0]
    assert snap['payable']==bill['payable']
    assert client.get('/api/v2/advances',headers=owner).json()[0]['outstanding']=='400.00'
    assert client.get('/api/v2/anamat/'+str(customer),headers=owner).json()['balance']=='10.00'
    assert client.get('/api/v2/milk'+uri,headers=owner).json()['amount']=='400.00'
    # A dairy without dispatch records must not show a fabricated profit number.
    report=client.get('/api/v2/reports'+uri,headers=owner)
    assert report.status_code==200
    assert report.json()['estimated_surplus'] is None

def test_approved_milk_correction_recalculates_and_audits(client, owner):
    from decimal import Decimal
    from datetime import timedelta
    cid = client.post('/api/customers', json={'code':'M55','name':'Milk Farmer'},headers=owner).json()['id']
    d = date.today()-timedelta(days=7)
    chart = client.post('/api/rate-charts',headers=owner,json={
        'name':'Correction test chart','milk_type':'cow','chart_type':'FIXED_RATE',
        'effective_from':(d-timedelta(days=3)).isoformat(),'match_policy':'EXACT_MATCH',
        'fixed_rate':'40.00','entries':[]}).json()
    assert client.post(f'/api/rate-charts/{chart["id"]}/activate',headers=owner).status_code==200
    original={'customer_id':cid,'collection_date':d.isoformat(),'session':'morning',
              'milk_type':'cow','quantity':'5','fat':'4','snf':'8.5'}
    r=client.post('/api/v2/milk/manual',headers=owner,json=original)
    assert r.status_code==201,r.text
    row_id=r.json()['id']
    start,end=d-timedelta(days=2),d+timedelta(days=1)
    uri=f'?start={start.isoformat()}&end={end.isoformat()}'
    listed=client.get('/api/v2/milk'+uri,headers=owner)
    assert listed.status_code==200
    version=listed.json()['rows'][0]['version']
    modified={**original,'quantity':'7','expected_version':version}
    correction=client.put(f'/api/v2/milk/{row_id}',headers=owner,json=modified)
    assert correction.status_code==200,correction.text
    assert Decimal(correction.json()['amount'])==Decimal('280.00')
    conflict=client.put(f'/api/v2/milk/{row_id}',headers=owner,json=modified)
    assert conflict.status_code==409
    statement=client.get(f'/api/customers/{cid}',headers=owner)
    assert statement.status_code==200
    assert Decimal(statement.json()['balance'])==Decimal('280.00')
    assert client.post('/api/v2/billing/finalize',headers=owner,
           json={'start':start.isoformat(),'end':end.isoformat()}).status_code==200
    later=client.put(f'/api/v2/milk/{row_id}',headers=owner,
        json={**modified,'expected_version':correction.json()['version'],'quantity':'8'})
    assert later.status_code==409


def test_feed_register_approval_is_atomic_and_idempotent(client, owner):
    from app.database import SessionLocal
    from app.models import Customer, ScanBatch, ScanRow
    farmer_id=client.post('/api/customers',json={'code':'F55','name':'Feed Farmer'},headers=owner).json()['id']
    prod=client.post('/api/feed/products',headers=owner,json={
        'product_name':'Feed Test','product_code':'FT-1','category':'Concentrate','unit':'BAG',
        'purchase_price':'500','selling_price':'700','low_stock_threshold':'1'})
    assert prod.status_code==201,prod.text
    pid=prod.json()['id']
    bought=client.post('/api/v2/feed/stock/bulk',headers=owner,json={
        'purchase_date':date.today().isoformat(),'idempotency_key':'feed-stock-test-uid-1000',
        'items':[{'product_id':pid,'quantity':'5','purchase_rate':'500','selling_price':'720'}]})
    assert bought.status_code==200,bought.text
    db=SessionLocal()
    try:
        customer=db.get(Customer,farmer_id)
        batch=ScanBatch(dairy_id=customer.dairy_id,kind='feed',filename='mock-register.jpg',
            status='pending',image_layout={},raw_text='F55 FT-1 2')
        db.add(batch);db.flush()
        row=ScanRow(dairy_id=customer.dairy_id,batch_id=batch.id,customer_id=farmer_id,
            extracted_code='F55',extracted_name='Feed Farmer',quantity=2,confidence=.98,
            extracted_fields={'product_code':{'value':'FT-1','confidence':.98}},
            field_confidence={'quantity':.98},extraction_warnings=[],approval_status='pending')
        db.add(row);db.commit()
        batch_id,row_id=batch.id,row.id
    finally:db.close()
    endpoint=f'/api/v2/feed/scans/{batch_id}/approve'
    payload={'row_ids':[row_id],'sale_date':date.today().isoformat()}
    first=client.post(endpoint,headers=owner,json=payload)
    assert first.status_code==200,first.text
    assert Decimal(first.json()['approved'][0]['amount'])==Decimal('1440.00')
    again=client.post(endpoint,headers=owner,json=payload)
    assert again.status_code==200,again.text
    assert again.json()['approved'][0]['already_approved'] is True
    products=client.get('/api/feed/products',headers=owner).json()
    assert Decimal(products[0]['available_quantity'])==Decimal('3.000')

def test_unpaid_negative_balance_carries_into_next_period(client, owner):
    from datetime import timedelta
    cid=client.post('/api/customers',json={'code':'CF001','name':'Carry Farmer'},headers=owner).json()['id']
    day=date.today()-timedelta(days=28)
    first_start,first_end=day,day+timedelta(days=5)
    second_start,second_end=day+timedelta(days=6),day+timedelta(days=11)
    p=client.post('/api/feed/products',headers=owner,json={'product_name':'Concentrate',
        'product_code':'CARRY-1','category':'','unit':'BAG','purchase_price':'100',
        'selling_price':'150','low_stock_threshold':'0'})
    assert p.status_code==201,p.text
    pid=p.json()['id']
    assert client.post('/api/v2/feed/stock/bulk',headers=owner,json={'purchase_date':str(day),
        'idempotency_key':'carry-stock-00001','items':[{'product_id':pid,'quantity':2,'purchase_rate':100}]}).status_code==200
    sale=client.post('/api/feed/sales',headers=owner,json={'sale_date':str(day),
        'customer_id':cid,'idempotency_key':'carry-sale-00001',
        'items':[{'product_id':pid,'quantity':1}]})
    assert sale.status_code==201,sale.text
    u1=f'?start={first_start}&end={first_end}'
    prev1=client.get('/api/v2/billing/preview'+u1,headers=owner).json()
    b1=next(b for b in prev1['bills'] if b['customer_id']==cid)
    assert b1['carry_forward']=='150.00'
    assert client.post('/api/v2/billing/finalize',headers=owner,json={'start':str(first_start),
        'end':str(first_end)}).status_code==200
    # A later bill includes prior unpaid negative settlement, with no new transactions.
    u2=f'?start={second_start}&end={second_end}'
    prev2=client.get('/api/v2/billing/preview'+u2,headers=owner).json()
    b2=next(b for b in prev2['bills'] if b['customer_id']==cid)
    assert b2['opening_carry_forward']=='150.00'
    assert b2['payable']=='-150.00'

def test_structured_rate_chart_scan_persists_metadata(client, owner):
    from app.database import SessionLocal
    from app.models import Dairy
    from app.services.extraction import ExtractionService
    class FakePreprocessed:
        processed_image=b'mock'
        cells=[]
        def layout(self):return {'cells':[],'table_regions':[],'warnings':[]}
    class FakePreprocessor:
        def process(self, image_bytes):return FakePreprocessed()
    class FakeOCR:
        def recognize(self, image_bytes, cells, register_type):
            assert register_type=='RATE_CHART'
            return {'text':'FAT SNF pricing matrix','words':[],'provider':'fake-vision',
                'metadata':{'model':'test-vision'},'cells':[],
                'structured_extraction':{
                    'register_type':'RATE_CHART','raw_text':'FAT SNF pricing matrix',
                    'fat_values':[{'value':'4.0','confidence':1}],
                    'snf_values':[{'value':'8.5','confidence':1}],
                    'matrix_cells':[{'fat_value':{'value':'4.0'},
                                     'snf_value':{'value':'8.5'},'rate':{'value':'42','confidence':.95}}],
                    'warnings':[],'extraction_errors':[]}}
    db=SessionLocal()
    try:
        dairy=db.query(Dairy).filter_by(dairy_code='TEST001').first()
        batch,_,_=ExtractionService(db,dairy.id,FakePreprocessor(),FakeOCR()).scan(
            b'fake image', 'sample.png','rate_chart','image/png')
        id=batch.id
        db.commit()
        db.expire_all()
        from app.models import ScanBatch
        saved=db.get(ScanBatch,id)
        assert saved.image_layout['structured_extraction']['matrix_cells'][0]['rate']['value']=='42'
        assert saved.original_image==b'fake image'
        assert saved.image_layout['ocr']['provider']=='fake-vision'
    finally:db.close()

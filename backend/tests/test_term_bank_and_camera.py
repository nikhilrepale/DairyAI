"""Bank export, customer corrections, tenant privacy. Uses only fictional data."""
import io
import json
import os
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile
import pytest
from cryptography.fernet import Fernet

@pytest.fixture()
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_URL',f'sqlite:///{tmp_path / "app.db"}')
    monkeypatch.setenv('JWT_SECRET_KEY','unit-test-secret-'+'x'*60)
    monkeypatch.setenv('BANK_DATA_KEY',Fernet.generate_key().decode())
    for name in list(sys.modules):
        if name=='app' or name.startswith('app.'):
            del sys.modules[name]
    from app.database import Base,engine
    from app import models
    Base.metadata.create_all(engine)
    from app.main import app
    from fastapi.testclient import TestClient
    with TestClient(app) as test:yield test
    engine.dispose()

@pytest.fixture()
def setup(client):
    a=client.post('/api/auth/register',json={'dairy_name':'Bank Pilot','dairy_code':'BANK-01','name':'Owner',
        'email':'pilot@example.test','password':'long-test-password-999'})
    assert a.status_code==201,a.text
    auth={'Authorization':'Bearer '+a.json()['access_token']}
    c=client.post('/api/customers',headers=auth,json={'code':'F01','name':'Sample Farmer'})
    assert c.status_code==200,c.text
    farmer_id=c.json()['id']
    day=date.today()-timedelta(days=12)
    chart=client.post('/api/rate-charts',headers=auth,json={'name':'Test chart','milk_type':'cow',
        'chart_type':'FAT_SNF_MATRIX','effective_from':(day-timedelta(days=2)).isoformat(),
        'match_policy':'EXACT_MATCH','entries':[{'fat_value':'4.0','snf_value':'8.5','rate':'40.00'}]})
    assert chart.status_code==201,chart.text
    assert client.post(f'/api/rate-charts/{chart.json()["id"]}/activate',headers=auth).status_code==200
    saved=client.post('/api/v2/milk/manual',headers=auth,json={'customer_id':farmer_id,'collection_date':day.isoformat(),
        'session':'morning','milk_type':'cow','quantity':'10','fat':'4','snf':'8.5'})
    assert saved.status_code==201,saved.text
    return auth,farmer_id,day,saved.json()['id']

def test_term_board_recalculates_after_correction_and_freezes(setup,client):
    auth,farmer,day,rowid=setup
    start,end=day-timedelta(days=1),day+timedelta(days=1)
    path=f'/api/v2/term-board?start={start}&end={end}'
    first=client.get(path,headers=auth)
    assert first.status_code==200,first.text
    rec=next(x for x in first.json()['farmers'] if x['customer_id']==farmer)
    assert Decimal(rec['milk_amount'])==Decimal('400.00')
    assert Decimal(rec['payable'])==Decimal('400.00')
    assert Decimal(rec['days'][day.isoformat()]['litres'])==Decimal('10')
    assert rec['days'][(day+timedelta(days=1)).isoformat()]['litres']=='0'
    original=client.get(f'/api/v2/milk?start={start}&end={end}',headers=auth).json()['rows'][0]
    corrected=client.put(f'/api/v2/milk/{rowid}',headers=auth,json={'expected_version':original['version'],
        'customer_id':farmer,'collection_date':day.isoformat(),'session':'morning','milk_type':'cow',
        'quantity':'12','fat':'4.0','snf':'8.5'})
    assert corrected.status_code==200,corrected.text
    assert Decimal(corrected.json()['amount'])==Decimal('480.00')
    second=client.get(path,headers=auth).json()
    rec2=next(x for x in second['farmers'] if x['customer_id']==farmer)
    assert Decimal(rec2['payable'])==Decimal('480.00')
    period=client.post('/api/v2/billing/finalize',headers=auth,json={'start':str(start),'end':str(end)})
    assert period.status_code==200,period.text
    third=client.get(path,headers=auth).json()
    assert third['finalized'] is True
    assert Decimal(next(x for x in third['farmers'] if x['customer_id']==farmer)['payable'])==Decimal('480.00')
    corrected_again=client.put(f'/api/v2/milk/{rowid}',headers=auth,json={'expected_version':corrected.json()['version'],
        'customer_id':farmer,'collection_date':day.isoformat(),'session':'morning','milk_type':'cow',
        'quantity':'13','fat':'4','snf':'8.5'})
    assert corrected_again.status_code==409

def test_bank_export_no_missing_accounts_no_automatic_payment(setup,client):
    auth,farmer,day,_=setup
    start,end=day-timedelta(days=1),day+timedelta(days=1)
    finalized=client.post('/api/v2/billing/finalize',headers=auth,json={'start':str(start),'end':str(end)})
    assert finalized.status_code==200,finalized.text
    period=finalized.json()['period_id']
    pre=client.get(f'/api/v2/bank/billing/{period}/preview',headers=auth)
    assert pre.status_code==200
    assert any(x['code']=='DAIRY' for x in pre.json()['missing'])
    assert client.post(f'/api/v2/bank/billing/{period}/export',headers=auth).status_code==409
    deb=client.put('/api/v2/bank/dairy',headers=auth,json={'beneficiary_name':'Sample Dairy',
        'account_number':'000111222333','confirm_account_number':'000111222333','ifsc':'ABCD0123456'})
    assert deb.status_code==200,deb.text
    assert deb.json()['masked_account'].endswith('2333')
    ben=client.put(f'/api/v2/bank/customers/{farmer}',headers=auth,json={'beneficiary_name':'Sample Farmer',
        'account_number':'000000098765','confirm_account_number':'000000098765','ifsc':'EFGH0123456'})
    assert ben.status_code==200,ben.text
    assert '000000098765' not in ben.text
    pre=client.get(f'/api/v2/bank/billing/{period}/preview',headers=auth)
    assert pre.json()['ready']==1
    assert pre.json()['total']=='400.00'
    file=client.post(f'/api/v2/bank/billing/{period}/export',headers=auth)
    assert file.status_code==200,file.text[:200] if file.status_code!=200 else ''
    assert 'spreadsheetml' in file.headers['content-type']
    assert file.headers['cache-control']=='no-store'
    with ZipFile(io.BytesIO(file.content)) as zipped:
        content=zipped.read('xl/worksheets/sheet1.xml').decode()
        parsed=ET.fromstring(content)
        assert '000000098765' in content
        assert '000111222333' in content
        assert '400.00' in content
        account_cell=parsed.find('.//{*}c[@r="E2"]')
        assert account_cell.attrib['t']=='inlineStr'
        assert account_cell.find('.//{*}t').text=='000000098765'
        def cv(ref):
            found=parsed.find(f'.//{{*}}c[@r="{ref}"]')
            return found.find('.//{*}t').text if found is not None else None
        from app.services.banking import ICICI_HEADERS
        assert [cv(f'{chr(65+i)}1') for i in range(19)] == list(ICICI_HEADERS)
        assert cv('A2') == 'PAB_VENDOR'
        assert cv('B2') == 'NEFT'
        assert cv('C2') == '000111222333'
        assert cv('D2') == 'Sample Farmer'
        assert cv('G2') == '400.00'
        assert cv('M2') == date.today().strftime('%d-%m-%Y')
    again=client.get(f'/api/v2/bank/billing/{period}/preview',headers=auth)
    assert again.json()['exported_before'] is True
    # An export is *not* a payment acknowledgement.
    assert again.json()['format']=='ICICI_PAB_VENDOR_19_COLUMNS_SAMPLE_MATCH'
    other=client.post('/api/auth/register',json={'dairy_name':'Other','dairy_code':'OTHER-BANK','name':'Other',
       'email':'other@example.test','password':'long-test-password-123'})
    h2={'Authorization':'Bearer '+other.json()['access_token']}
    assert client.get(f'/api/v2/bank/customers/{farmer}',headers=h2).status_code==404
    assert client.post(f'/api/v2/bank/billing/{period}/export',headers=h2).status_code==409

def test_farmer_creation_with_bank_data_is_encrypted(setup,client):
    auth,_,_,_=setup
    r=client.post('/api/customers',headers=auth,json={'code':'F02','name':'Bank Customer',
       'bank_details':{'beneficiary_name':'Bank Customer','account_number':'000022330099',
       'confirm_account_number':'000022330099','ifsc':'ABCD0123456'}})
    assert r.status_code==200,r.text
    from app.database import SessionLocal
    from app.models import Customer
    with SessionLocal() as db:
        f=db.query(Customer).filter_by(code='F02').one()
        assert '000022330099' not in f.bank_details_encrypted
        assert f.bank_details_encrypted.startswith('gAAAA')


def test_icici_alphanumeric_accounts_ft_mode_and_explicit_date(setup,client):
    auth,farmer,day,_=setup
    start,end=day-timedelta(days=1),day+timedelta(days=1)
    period=client.post('/api/v2/billing/finalize',headers=auth,json={'start':str(start),'end':str(end)}).json()['period_id']
    assert client.put('/api/v2/bank/dairy',headers=auth,json={
        'beneficiary_name':'Test Dairy','account_number':'000000123456',
        'confirm_account_number':'000000123456','ifsc':'ICIC0000104'}).status_code==200
    result=client.put(f'/api/v2/bank/customers/{farmer}',headers=auth,json={
        'beneficiary_name':'Example Farmer','account_number':'N123456789000009',
        'confirm_account_number':'N123456789000009','ifsc':'ICIC0000104',
        'payment_mode':'FT'})
    assert result.status_code==200,result.text
    assert result.json()['payment_mode']=='FT'
    exported=client.post(f'/api/v2/bank/billing/{period}/export?payment_date=2026-10-16',headers=auth)
    assert exported.status_code==200,exported.text[:300] if exported.status_code!=200 else ''
    with ZipFile(io.BytesIO(exported.content)) as archive:
        parsed=ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
        def txt(ref):
            cell=parsed.find(f'.//{{*}}c[@r="{ref}"]')
            return cell.find('.//{*}t').text
        assert txt('B2')=='FT'
        assert txt('E2')=='N123456789000009'
        assert txt('M2')=='16-10-2026'
        assert len(parsed.find('.//{*}row[@r="1"]').findall('{*}c'))==19
        assert 'xl/worksheets/sheet2.xml' in archive.namelist()

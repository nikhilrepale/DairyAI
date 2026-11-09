"""Encryption of bank details and a standard, non-bank-specific XLSX exporter.

Generating an XLSX does not send money or mark anyone paid. The bank's
actual import template must be verified before any bank upload.
"""
from __future__ import annotations
import io
import json
import os
import re
from decimal import Decimal
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED
from cryptography.fernet import Fernet, InvalidToken

class BankConfigurationError(ValueError): pass

def _cipher():
    key=os.getenv('BANK_DATA_KEY','').strip()
    if not key: raise BankConfigurationError('BANK_DATA_KEY missing: generate a Fernet key and keep it as a deployment secret.')
    try: return Fernet(key.encode('ascii'))
    except (ValueError,TypeError) as exc: raise BankConfigurationError('Invalid BANK_DATA_KEY Fernet key') from exc

def encrypt_details(details):
    return _cipher().encrypt(json.dumps(details,ensure_ascii=False).encode()).decode('ascii')

def decrypt_details(encrypted):
    if not encrypted: return None
    try: return json.loads(_cipher().decrypt(encrypted.encode()).decode())
    except InvalidToken as exc: raise BankConfigurationError('Cannot decrypt bank details; restore the original BANK_DATA_KEY') from exc

def masked_details(details):
    if not details: return {'configured':False,'masked_account':'','beneficiary_name':'','ifsc':'','bank_name':'','account_type':''}
    account=details['account_number']
    return {'configured':True,'masked_account':'•'*max(0,len(account)-4)+account[-4:],
        'beneficiary_name':details['beneficiary_name'],'ifsc':details['ifsc'],
        'bank_name':details.get('bank_name',''),'account_type':details.get('account_type',''),'payment_mode':details.get('payment_mode','NEFT')}

def validate_details(details):
    account=re.sub(r'[ -]','',str(details.get('account_number','')).strip()).upper()
    confirmation=re.sub(r'[ -]','',str(details.get('confirm_account_number','')).strip()).upper()
    if not re.fullmatch(r'[A-Z0-9]{6,24}',account): raise BankConfigurationError('Account number must be 6–24 letters/digits; leading zeros are preserved')
    if account!=confirmation: raise BankConfigurationError('Account number confirmation does not match')
    ifsc=str(details.get('ifsc','')).upper().strip()
    if not re.fullmatch(r'[A-Z]{4}0[A-Z0-9]{6}',ifsc): raise BankConfigurationError('IFSC must have 11 characters (e.g. ABCD0123456)')
    name=str(details.get('beneficiary_name','')).strip()
    if not 2<=len(name)<=120: raise BankConfigurationError('Account holder name is required')
    return {'account_number':account,'ifsc':ifsc,'beneficiary_name':name,
            'bank_name':str(details.get('bank_name','')).strip()[:120],
            'account_type':str(details.get('account_type','')).strip()[:30],
            'payment_mode':_validate_payment_mode(details.get('payment_mode','NEFT'))}

# Column order taken from the user-provided ICICI PAB payment-upload example.
# Do not include that confidential example file in the application package.
ICICI_HEADERS = (
    "PYMT_PROD_TYPE_CODE", "PYMT_MODE", "DEBIT_ACC_NO", "BNF_NAME",
    "BENE_ACC_NO", "BENE_IFSC", "AMOUNT", "DEBIT_NARR", "CREDIT_NARR",
    "MOBILE_NUM", "EMAIL_ID", "REMARK", "PYMT_DATE", "REF_NO",
    "ADDL_INFO1", "ADDL_INFO2", "ADDL_INFO3", "ADDL_INFO4", "ADDL_INFO5",
)


def _validate_payment_mode(value):
    mode = str(value or "NEFT").strip().upper()
    if mode not in {"NEFT", "FT", "RTGS"}:
        raise BankConfigurationError("Payment mode must be NEFT, FT, or RTGS")
    return mode


def _xl_col(index):
    result = ""
    while index:
        index, rem = divmod(index - 1, 26)
        result = chr(65 + rem) + result
    return result


def _xml_cell(column, row_number, value, style=None):
    ref = f"{_xl_col(column)}{row_number}"
    style_attr = f' s="{style}"' if style is not None else ""
    # All values in the provided ICICI example, including account numbers and
    # AMOUNT, are string cells. Preserve that exact typing and leading zeros.
    # Inline strings also prevent the user's text from becoming XLSX formulas.
    data = escape(str(value if value is not None else ""))
    return f'<c r="{ref}" t="inlineStr"{style_attr}><is><t xml:space="preserve">{data}</t></is></c>'


def build_payment_xlsx(rows, payment_date=None):
    """Generate the 19-column ICICI PAB_VENDOR upload layout, without sending money.

    The bank example had Sheet1 containing the data and an empty Sheet2.
    No real bank details or values from the example workbook are embedded here.
    """
    from datetime import date
    from zipfile import ZipInfo

    payment_date = payment_date or date.today()
    if not isinstance(payment_date, date):
        raise BankConfigurationError("payment_date must be a date")
    date_text = payment_date.strftime("%d-%m-%Y")

    lines = ['<row r="1" ht="25" customHeight="1">'
             + ''.join(_xml_cell(i, 1, header, 1) for i, header in enumerate(ICICI_HEADERS, 1))
             + '</row>']
    for row_number, item in enumerate(rows, 2):
        amount = Decimal(str(item['amount']))
        if not amount.is_finite() or amount <= 0:
            raise BankConfigurationError("A bank payment row must have a positive amount")
        # Payment mode is an explicit per-farmer choice, not an IFSC guess:
        # the provided file contains ICICI beneficiaries with both FT and NEFT.
        fields = [
            "PAB_VENDOR", _validate_payment_mode(item.get('payment_mode','NEFT')),
            item['debit_account'], item['name'], item['account'], item['ifsc'],
            f"{amount:.2f}", " ", " ", item.get('mobile','') or " ",
            " ", " ", date_text, " ", " ", " ", " ", " ", " ",
        ]
        lines.append(f'<row r="{row_number}">' + ''.join(
            _xml_cell(i,row_number,value) for i,value in enumerate(fields,1)) + '</row>')

    widths=[23,14,22,34,26,19,16,22,22,18,24,24,19,18,16,16,16,16,16]
    columns=''.join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>'
                    for i,w in enumerate(widths,1))
    worksheet=('<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        '<cols>'+columns+'</cols><sheetData>'+''.join(lines)+'</sheetData>'
        +f'<autoFilter ref="A1:S{len(rows)+1}"/></worksheet>')
    empty_sheet=('<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData/></worksheet>')
    types=('<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
    rels=('<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    workbook=('<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/><sheet name="Sheet2" sheetId="2" r:id="rId2"/></sheets></workbook>')
    wbrel=('<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
    styles=('<?xml version="1.0" encoding="UTF-8"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="3"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF176D48"/><bgColor indexed="64"/></patternFill></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs></styleSheet>')
    payloads={'[Content_Types].xml':types,'_rels/.rels':rels,'xl/workbook.xml':workbook,
        'xl/_rels/workbook.xml.rels':wbrel,'xl/styles.xml':styles,
        'xl/worksheets/sheet1.xml':worksheet,'xl/worksheets/sheet2.xml':empty_sheet}
    output=io.BytesIO()
    with ZipFile(output,'w',compression=ZIP_DEFLATED) as archive:
        for path,content in payloads.items():
            entry=ZipInfo(path,date_time=(2000,1,1,0,0,0))
            entry.compress_type=ZIP_DEFLATED
            archive.writestr(entry,content.encode('utf-8'))
    return output.getvalue()

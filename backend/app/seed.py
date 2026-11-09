from decimal import Decimal
from datetime import date, timedelta
from .models import Customer, RateRule, LedgerEntry

def seed(db, dairy_id: int):
    if db.query(Customer).filter_by(dairy_id=dairy_id).count(): return
    people=[
        ("D001","राम पाटील","9876543210","पिंपळगाव"),
        ("D002","सीमा शिंदे","9876543211","पिंपळगाव"),
        ("D003","विजय जाधव","9876543212","वडगाव"),
        ("D004","सुनीता देशमुख","9876543213","वडगाव"),
        ("D005","अमोल मोरे","9876543214","पिंपळगाव"),
        ("D006","कविता पवार","9876543215","नांदगाव"),
        ("D007","सचिन गायकवाड","9876543216","नांदगाव"),
        ("D008","मंगेश काळे","9876543217","वडगाव"),
    ]
    for code,name,phone,village in people:
        db.add(Customer(dairy_id=dairy_id, code=code,name=name,phone=phone,village=village))
    db.flush()
    # Illustrative placeholder only. Replace with the dairy's actual chart before real billing.
    for lo,hi,base in [(0,3,Decimal("28")),(3.01,4,Decimal("30")),(4.01,5,Decimal("32")),(5.01,6,Decimal("34")),(6.01,10,Decimal("36"))]:
        db.add(RateRule(dairy_id=dairy_id,milk_type="cow",fat_min=lo,fat_max=hi,base_rate=base,
                        per_fat_rate=Decimal("0"),snf_bonus=Decimal("0.50")))
    customers=db.query(Customer).filter_by(dairy_id=dairy_id).all()
    today=date.today()
    demo=[
        ("milk_credit",Decimal("420"),"Demo milk collection","seed-milk-1",customers[0].id),
        ("feed_debit",Decimal("-120"),"Cattle feed purchase","seed-feed-1",customers[0].id),
        ("advance_disbursed",Decimal("-1000"),"Advance paid","seed-adv-1",customers[0].id),
        ("milk_credit",Decimal("510"),"Demo milk collection","seed-milk-2",customers[1].id),
        ("feed_debit",Decimal("-80"),"Cattle feed purchase","seed-feed-2",customers[1].id),
    ]
    for typ,amt,desc,ref,cid in demo:
        db.add(LedgerEntry(dairy_id=dairy_id,customer_id=cid,entry_type=typ,amount=amt,description=desc,
                           reference=ref,entry_date=today-timedelta(days=1)))
    db.commit()

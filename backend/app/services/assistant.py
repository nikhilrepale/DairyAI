import os, requests
from sqlalchemy.orm import Session
from ..models import Customer
from .ledger import LedgerService

class AssistantService:
    """LLM interprets Marathi intent; tools/data access remain deterministic and server-side."""
    def __init__(self, db: Session, dairy_id: int): self.db, self.dairy_id=db, dairy_id

    def answer(self, query):
        customer, intent = self._interpret_query(query)
        if not customer:
            return {"answer":"कृपया ग्राहकाचे पूर्ण नाव किंवा ग्राहक क्रमांक सांगा. (Please mention the exact customer name/code.)",
                    "customer":None}
        stmt=LedgerService(self.db, self.dairy_id).statement(customer.id)
        entries=stmt["entries"]
        total_milk=sum(float(e.amount) for e in entries if e.entry_type=="milk_credit")
        feed=sum(-float(e.amount) for e in entries if e.entry_type=="feed_debit")
        advance=sum(-float(e.amount) for e in entries if e.entry_type=="advance_disbursed")
        interest=sum(-float(e.amount) for e in entries if e.entry_type=="advance_interest")
        balance=float(stmt["balance"])
        facts={"name":customer.name,"code":customer.code,"intent":intent,"balance":balance,
               "milk_credits":round(total_milk,2),"feed_deductions":round(feed,2),
               "advances_taken":round(advance,2),"interest_deducted":round(interest,2)}
        if os.getenv("GEMINI_API_KEY") and os.getenv("GEMINI_MODEL", "").strip():
            try:
                model=os.getenv("GEMINI_MODEL", "").strip()
                prompt=("Answer the dairy owner's question in Marathi if the question is Marathi, "
                        "otherwise use the query language. Use ONLY these server-computed customer facts; "
                        f"do not infer or invent. Facts: {facts}\nQuestion: {query}")
                url=f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={os.getenv('GEMINI_API_KEY')}"
                r=requests.post(url,json={"contents":[{"parts":[{"text":prompt}]}]},timeout=30)
                r.raise_for_status()
                answer=r.json()["candidates"][0]["content"]["parts"][0]["text"]
                return {"answer":answer,"customer":facts}
            except Exception:
                pass
        return {"answer":f"{customer.name} ({customer.code}) यांची सध्याची बाकी ₹{balance:,.2f} आहे. "
                f"दूध जमा ₹{total_milk:,.2f}, खाद्य कपात ₹{feed:,.2f}, आगाऊ रक्कम ₹{advance:,.2f}, "
                f"व्याज ₹{interest:,.2f}.", "customer":facts}

    def _interpret_query(self, query):
        customers=self.db.query(Customer).filter_by(
            dairy_id=self.dairy_id, active=True
        ).all()
        # LLM command/entity extraction: it may choose only from this registered-customer allowlist.
        if os.getenv("GEMINI_API_KEY") and os.getenv("GEMINI_MODEL", "").strip():
            try:
                model=os.getenv("GEMINI_MODEL", "").strip()
                candidates=[{"code":c.code,"name":c.name} for c in customers]
                prompt=("You are a Marathi dairy voice-command parser. Identify the one registered customer "
                        "the owner is asking about and classify the requested information. Return JSON only: "
                        "{customer_code: string|null, intent: balance|statement|milk|advance|feed|summary}. "
                        "Choose customer_code ONLY from the supplied list; if ambiguous or absent use null. "
                        f"Registered customers: {candidates}\nUtterance: {query}")
                url=f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={os.getenv('GEMINI_API_KEY')}"
                r=requests.post(url,json={"contents":[{"parts":[{"text":prompt}]}],
                                "generationConfig":{"responseMimeType":"application/json"}},timeout=25)
                r.raise_for_status()
                parsed=r.json()["candidates"][0]["content"]["parts"][0]["text"]
                import json
                data=json.loads(parsed)
                code=data.get("customer_code")
                intent=data.get("intent","summary")
                allowed={"balance","statement","milk","advance","feed","summary"}
                customer=next((c for c in customers if c.code==code),None)
                if customer and intent in allowed:
                    return customer,intent
            except Exception:
                pass
        # Deterministic fallback for local demo without an API key.
        q=query.casefold()
        customer=next((c for c in customers if c.name.casefold() in q or c.code.casefold() in q),None)
        intent="balance"
        if any(w in q for w in ("दूध","milk","litre","लिटर")): intent="milk"
        elif any(w in q for w in ("advance","आगाऊ","अ‍ॅडव्हान्स")): intent="advance"
        elif any(w in q for w in ("feed","खाद्य","चारा")): intent="feed"
        elif any(w in q for w in ("statement","हिशोब","तपशील")): intent="statement"
        return customer,intent

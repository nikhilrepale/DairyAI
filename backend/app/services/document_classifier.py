"""Classify document images with a configurable vision model.

Unknown/ambiguous classifications are never passed to a posting workflow.
"""
import base64
import io
import json
import os
import requests
from PIL import Image, ImageOps

class DocumentClassificationError(ValueError):
    pass

KINDS={
    'MILK_REGISTER':'milk',
    'CATTLE_FEED_PURCHASE':'feed',
    'RATE_CHART':'rate_chart',
    'UNKNOWN':'unknown',
}

def _classify_with_provider(provider: str, prompt: str, image_data: str) -> dict:
    key=os.getenv('GEMINI_API_KEY','').strip()
    model=(
        os.getenv('GEMINI_MODEL','gemini-3.8-flash').strip()
        if provider == 'gemini' else os.getenv('OLLAMA_MODEL','').strip()
    )
    if provider == 'gemini' and not key:
        raise DocumentClassificationError('GEMINI_API_KEY is not configured.')
    if provider == 'ollama' and not model:
        raise DocumentClassificationError('OLLAMA_MODEL is not configured.')

    response=None
    try:
        timeout_env=(
            'OLLAMA_CLASSIFICATION_TIMEOUT'
            if provider == 'ollama' else 'AI_CLASSIFICATION_TIMEOUT'
        )
        timeout_default=(
            os.getenv('AI_CLASSIFICATION_TIMEOUT','90')
            if provider == 'ollama' else '90'
        )
        timeout=max(1,int(os.getenv(timeout_env,timeout_default)))
        if provider == 'gemini':
            response=requests.post(
                f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
                headers={'x-goog-api-key':key},
                json={
                    'contents':[{'parts':[{'text':prompt},{'inline_data':{
                        'mime_type':'image/jpeg','data':image_data}}]}],
                    'generationConfig':{
                        'responseMimeType':'application/json','temperature':0,'maxOutputTokens':180,
                    },
                },
                timeout=timeout,
            )
            response.raise_for_status()
            content=response.json()['candidates'][0]['content']['parts'][0]['text']
        else:
            ollama_url=os.getenv('OLLAMA_BASE_URL','http://localhost:11434').rstrip('/')
            response=requests.post(
                f'{ollama_url}/api/chat',
                json={
                    'model':model,
                    'messages':[{'role':'user','content':prompt,'images':[image_data]}],
                    'format':'json','stream':False,'options':{'temperature':0},
                },
                timeout=timeout,
            )
            response.raise_for_status()
            content=response.json()['message']['content']
        answer=json.loads(content)
        if not isinstance(answer,dict):
            raise ValueError('Model did not return a JSON object')
        return answer
    except requests.RequestException as exc:
        if response is not None and provider == 'gemini':
            try:
                error_message=response.json().get('error',{}).get('message')
            except (ValueError,AttributeError,TypeError):
                error_message=None
            status=getattr(response,'status_code',None)
            detail=f'Gemini classification request failed (HTTP {status})'
            if isinstance(error_message,str) and error_message.strip():
                detail+=f': {error_message.strip()[:300].replace(key, "[redacted]")}'
        else:
            reason=str(exc).replace(key, '[redacted]') if provider == 'gemini' and key else str(exc)
            detail=f'{provider.title()} classification request failed ({type(exc).__name__})'
            if reason:
                detail+=f': {reason[:300]}'
        raise DocumentClassificationError(detail) from exc
    except (KeyError,IndexError,ValueError,TypeError) as exc:
        detail=f'{provider.title()} returned an empty or invalid classification response.'
        raise DocumentClassificationError(detail) from exc


def detect_kind(image_bytes: bytes) -> dict:
    provider=os.getenv('AI_PROVIDER','gemini').strip().lower()
    if provider not in {'gemini', 'ollama'}:
        raise DocumentClassificationError(
            'Automatic document detection needs AI_PROVIDER=gemini or ollama; '
            'select a document type manually for classical OCR.'
        )
    fallbacks=[
        item.strip().lower()
        for item in os.getenv('AI_FALLBACK_PROVIDERS','').split(',')
        if item.strip()
    ]
    unsupported=[item for item in fallbacks if item not in {'gemini','ollama'}]
    if unsupported:
        raise DocumentClassificationError(
            'AI_FALLBACK_PROVIDERS supports only gemini and ollama.'
        )
    providers=list(dict.fromkeys([provider,*fallbacks]))
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            img=ImageOps.exif_transpose(img).convert('RGB')
            img.thumbnail((1200,1200))
            output=io.BytesIO();img.save(output,format='JPEG',quality=77)
    except Exception as exc:
        raise DocumentClassificationError('Uploaded file is not a valid photograph') from exc
    prompt=(
      'Classify this dairy-related photo. Return STRICT JSON only with keys '
      'kind (one of MILK_REGISTER, CATTLE_FEED_PURCHASE, RATE_CHART, UNKNOWN), '
      'confidence (number 0 to 1), and reason (short string). '
      'MILK_REGISTER: farmer/customer IDs plus milk litres/FAT/SNF daily records. '
      'CATTLE_FEED_PURCHASE: farmer/customer IDs with cattle feed types and purchase quantities. '
      'RATE_CHART: a pricing table, often FAT rows and SNF columns with milk rates. '
      'UNKNOWN for ambiguous, unreadable, unsupported, or mixed document. '
      'Do NOT assume MILK_REGISTER just because the page contains a table.'
    )
    image_data=base64.b64encode(output.getvalue()).decode()
    failures=[]
    answer=None
    for selected_provider in providers:
        try:
            answer=_classify_with_provider(selected_provider,prompt,image_data)
            break
        except DocumentClassificationError as exc:
            failures.append(f'{selected_provider}: {exc}')
    if answer is None:
        raise DocumentClassificationError(
            'Automatic document classification failed: ' + ' | '.join(failures)
            + '. Select a type manually and retry.'
        )
    kind=str(answer.get('kind','UNKNOWN')).upper()
    try:confidence=float(answer.get('confidence',0))
    except (ValueError,TypeError):confidence=0
    if kind not in KINDS or confidence<0.75:
        raise DocumentClassificationError('Document type uncertain. Choose Milk, Feed sales, or Rate chart manually.')
    if kind=='UNKNOWN':raise DocumentClassificationError('Unsupported document. Choose a valid type or upload a clearer image.')
    return {'kind':KINDS[kind],'confidence':min(1,max(0,confidence)),
            'reason':str(answer.get('reason',''))[:150]}

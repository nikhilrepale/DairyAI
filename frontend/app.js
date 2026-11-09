/* DairyAI V2 — small screens, human approval, server-calculated money.
   No framework or build step; all calls are same-origin FastAPI requests. */
'use strict';
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const state = {token:sessionStorage.getItem('dairyai_token')||'',session:null,page:'home',lang:localStorage.getItem('dairyai_language')||'mr',
  customers:[],products:[],feedTab:'stock',scan:null,scanType:'auto',chartDraft:null,chartSource:null,rateMilk:'cow',currentChart:null,
  start:'',end:'',billing:null,editTarget:null,showAllFarmers:false,scanSelectedFile:null,editReturnPage:'milk',bankPeriod:null};
const t=(mr,en)=>state.lang==='mr'?mr:en;
const clean=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=v=>new Intl.NumberFormat(state.lang==='mr'?'mr-IN':'en-IN',{style:'currency',currency:'INR',maximumFractionDigits:2}).format(Number(v||0));
const num=v=>new Intl.NumberFormat(state.lang==='mr'?'mr-IN':'en-IN',{maximumFractionDigits:2}).format(Number(v||0));
function today(){const d=new Date();return [d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-')}
function termFor(iso){const [y,m,d]=iso.split('-').map(Number),last=new Date(y,m,0).getDate();return {start:`${y}-${String(m).padStart(2,'0')}-${String(d<=10?1:d<=20?11:21).padStart(2,'0')}`,end:`${y}-${String(m).padStart(2,'0')}-${String(d<=10?10:d<=20?20:last).padStart(2,'0')}`}}
Object.assign(state,termFor(today()));
function alertError(error){toast(error.message||String(error),true)}
function toast(message,isError=false){const n=$('#toast');n.textContent=message;n.style.background=isError?'#a23c32':'';n.classList.add('show');clearTimeout(toast.timer);toast.timer=setTimeout(()=>{n.classList.remove('show');n.style.background=''},3500)}
async function api(path,method='GET',body=null){const headers={};if(state.token)headers.Authorization=`Bearer ${state.token}`;
 if(body!==null && !(body instanceof FormData))headers['Content-Type']='application/json';
 const res=await fetch(path,{method,headers,body:body===null?undefined:body instanceof FormData?body:JSON.stringify(body)});
 let result;try{result=await res.json()}catch{result={detail:`Server returned ${res.status}`}}
 if(!res.ok){let d=result.detail||result.message||`HTTP ${res.status}`;if(Array.isArray(d))d=d.map(e=>e.msg||String(e)).join('; ');throw Error(typeof d==='string'?d:JSON.stringify(d))}
 return result;
}
const post=(p,b)=>api(p,'POST',b),put=(p,b)=>api(p,'PUT',b);
const run=async fn=>{try{return await fn()}catch(e){alertError(e);return null}};
const loading=()=>'<div class="loading">'+t('माहिती येत आहे…','Loading…')+'</div>';
const empty=(msg=t('नोंदी उपलब्ध नाहीत','Nothing to show yet'))=>`<div class="empty"><span class="symbol">☘</span>${clean(msg)}</div>`;
const pill=(text,type='')=>`<span class="pill ${type}">${clean(text)}</span>`;
const stat=(label,value,note='',icon='◈')=>`<div class="card statcard"><div class="statlabel">${label}<span class="stat-icon">${icon}</span></div><div class="statvalue">${value}</div><div class="statnote">${note}</div></div>`;
const panel=(heading,content,extra='')=>`<section class="panel"><div class="panelhead"><h2>${heading}</h2>${extra}</div>${content}</section>`;
const table=(heads,rows)=>`<div class="scrolltable"><table><thead><tr>${heads.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows||`<tr><td colspan="${heads.length}">${empty()}</td></tr>`}</tbody></table></div>`;
const btn=(text,action,css='outline',extra='')=>`<button class="btn ${css}" data-action="${action}" ${extra}>${text}</button>`;
const input=(id,label,value='',type='text',extra='')=>`<div class="field"><label for="${id}">${label}</label><input id="${id}" type="${type}" value="${clean(value)}" ${extra}></div>`;
const select=(id,label,options,value)=>`<div class="field"><label for="${id}">${label}</label><select id="${id}">${options.map(o=>`<option value="${clean(o[0])}" ${String(o[0])===String(value)?'selected':''}>${clean(o[1])}</option>`).join('')}</select></div>`;
const customerOptions=()=>[['',t('शेतकरी निवडा','Select farmer')],...state.customers.map(c=>[c.id,`${c.code} · ${c.name}`])];
const productOptions=()=>[['',t('पशुखाद्य निवडा','Select feed')],...state.products.map(p=>[p.id,`${p.product_name} · ${p.unit}`])];
const jsonField=(r,key)=>r.field_values?.[key]?.corrected_value??r.field_values?.[key]?.value??'';
const amountColor=(amount)=>Number(amount)<0?'negative':'positive';
const showModal=(title,content,foot='')=>{const d=$('#dialog');$('#dialogBody').innerHTML=`<div class="dialog-inner"><div class="dialog-title"><h2>${title}</h2><button class="iconbutton" data-action="close" aria-label="Close">×</button></div><div>${content}</div>${foot?`<div class="dialog-foot">${btn(t('रद्द','Cancel'),'close')}${foot}</div>`:''}</div>`;if(!d.open)d.showModal()};
const closeModal=()=>{$('#dialog').close()};
const value=id=>$('#'+id)?.value??'';
const check=id=>$('#'+id)?.checked??false;
const navItems=[['home','⌂','मुख्यपृष्ठ','Home'],['milk','◉','दूध नोंद','Milk'],['scan','▤','AI स्कॅन','AI Scan'],['feed','▣','पशुखाद्य','Cattle Feed'],['customers','♙','शेतकरी','Farmers'],['billing','₹','१० दिवसांचा हिशोब','Billing'],['rates','⌗','दर पत्रक','Rate Chart'],['advances','↗','अॅडव्हान्स','Advances'],['reports','▥','व्यवसाय अहवाल','Reports'],['settings','⚙','सेटिंग्ज','Settings'],['assistant','◎','मराठी सहाय्यक','Assistant']];
const pageLabel=id=>{const item=navItems.find(x=>x[0]===id);return item?t(item[2],item[3]):id};
function buildNav(){const current=state.page;
 $('#sideNav').innerHTML=navItems.map(x=>`<button class="navbutton ${x[0]===current?'active':''}" data-page="${x[0]}"><span class="symbol">${x[1]}</span>${t(x[2],x[3])}</button>`).join('');
 $('#mobileNav').innerHTML=['home','milk','scan','feed','billing'].map(key=>{const x=navItems.find(i=>i[0]===key);return `<button class="${key===current?'active':''}" data-page="${key}"><span class="symbol">${x[1]}</span><span>${t(x[2],x[3])}</span></button>`}).join('');
 $('#pageTitle').textContent=pageLabel(current);$('#breadcrumb').textContent=`DAIRYAI / ${state.session?.dairy?.name||'DAIRY'}`;
 $('#langButton').textContent=state.lang==='mr'?'EN':'मराठी';$('#dairyName').textContent=state.session?.dairy?.name||'DairyAI';
 $('#topTerm').textContent=`${state.start} — ${state.end}`;
}
async function go(page){state.page=page;buildNav();$('#view').innerHTML=loading();window.scrollTo({top:0,behavior:'instant'});
 const routes={home:homePage,milk:milkPage,scan:scanPage,feed:feedPage,customers:customersPage,billing:billingPage,
  rates:ratesPage,advances:advancesPage,reports:reportsPage,settings:settingsPage,assistant:assistantPage};
 await run(async()=>{await routes[page]();});}
async function refreshCustomers(){state.customers=await api('/api/customers')}
async function refreshProducts(){state.products=await api('/api/feed/products')}
async function init(){if(!state.token){authPage();return}try{state.session=await api('/api/auth/me');$('#auth').hidden=true;$('#shell').hidden=false;await refreshCustomers();await go('home');}catch(e){state.token='';sessionStorage.removeItem('dairyai_token');authPage()}}
let registering=false;
function authPage(){state.session=null;$('#shell').hidden=true;$('#auth').hidden=false;
 $('#auth').innerHTML=`<section class="auth-card"><div class="auth-head"><div class="brandmark">✦</div><div><b style="font-size:23px">DairyAI</b><small style="display:block;color:#788b7c">${t('तुमची डेअरी · तुमचा सोपा हिशोब','Your dairy · simplified')}</small></div></div>
 <h1>${registering?t('नवीन डेअरी तयार करा','Create dairy account'):t('डेअरीमध्ये स्वागत आहे','Welcome back')}</h1>
 <p>${registering?t('तुमची माहिती सुरक्षित डेअरी खात्यात जतन होईल.','Create your private dairy workspace.'):t('दूध, पशुखाद्य आणि १० दिवसांचा हिशोब — एकाच ठिकाणी.','Milk, feed and settlements in one place.')}</p>
 <form id="authForm" class="stack">${registering?`${input('dairyTitle',t('डेअरीचे नाव','Dairy name'),'','','required minlength="2"')}${input('dairyCode',t('डेअरी कोड','Dairy code'),'','','required minlength="3"')}${input('ownerName',t('तुमचे नाव','Your name'),'','','required minlength="2"')}`:''}
 ${input('identifier',registering?t('ईमेल','Email'):t('ईमेल किंवा मोबाईल','Email or mobile'),'','text',registering?'':'required')}
 ${registering?input('phone',t('मोबाईल (ऐच्छिक)','Mobile (optional)'),'','tel'):input('loginDairy',t('डेअरी कोड (अनेक डेअरी असल्यास)','Dairy code (if multiple)'),'','text')}
 ${input('password',t('पासवर्ड','Password'),'','password',registering?'required minlength="12"':'required')}
 <button type="submit" class="btn primary">${registering?t('खाते तयार करा','Create account'):t('लॉग इन करा','Sign in')} →</button></form>
 <button class="auth-switch" data-action="authMode">${registering?t('खाते आहे? लॉग इन करा','Already have an account? Sign in'):t('नवीन डेअरी नोंदवा','Create a dairy account')}</button>
 <button class="auth-switch" data-action="language">${state.lang==='mr'?'English':'मराठी'}</button></section>`;
 $('#authForm').onsubmit=async e=>{e.preventDefault();await run(async()=>{
 let result;if(registering){result=await post('/api/auth/register',{dairy_name:value('dairyTitle'),dairy_code:value('dairyCode'),name:value('ownerName'),email:value('identifier')||null,phone:value('phone')||null,password:value('password'),preferred_language:state.lang})}
 else{result=await post('/api/auth/login',{identifier:value('identifier'),password:value('password'),dairy_code:value('loginDairy')||null})}
 state.token=result.access_token;sessionStorage.setItem('dairyai_token',state.token);await init();});};
}
function termControls(action='changeTerm'){return `<div class="filterbar"><div class="field"><label>${t('पासून','From')}</label><input id="periodStart" type="date" value="${state.start}"></div><div class="field"><label>${t('पर्यंत','To')}</label><input id="periodEnd" type="date" value="${state.end}"></div>${btn(t('दाखवा','View'),action,'primary')} ${btn(t('सध्याचा हिशोब','Current term'),'currentTerm')}</div>`}
function actionTile(symbol,label,note,page){return `<button class="action-tile" data-page="${page}"><span class="big">${symbol}</span><b>${label}</b><small>${note}</small></button>`}
async function homePage(){
 const [summary,board,milk]=await Promise.all([
  api('/api/v2/home'),
  api(`/api/v2/term-board?start=${state.start}&end=${state.end}`),
  api(`/api/v2/milk?start=${state.start}&end=${state.end}`)
 ]);
 state.termBoard=board;state.milkRows=milk.rows;
 const visible=board.farmers.filter(f=>state.showAllFarmers||Number(f.litres)>0||Number(f.payable)!==0);
 const days=board.dates;
 const gridRows=visible.map(f=>`<tr class="term-row" data-find="${clean((f.name+' '+f.code).toLowerCase())}">
  <td class="sticky-farmer"><button class="farmer-link" data-action="homeFarmer" data-id="${f.customer_id}"><b>${clean(f.name)}</b><small>${clean(f.code)}</small></button></td>
  ${days.map(d=>`<td class="day-cell">${Number(f.days[d].litres)?`<button class="day-tap" data-action="homeFarmer" data-id="${f.customer_id}" data-date="${d}" title="${clean(d)} · ${money(f.days[d].amount)}">${num(f.days[d].litres)}<small>L</small></button>`:'<span class="quiet-zero">—</span>'}</td>`).join('')}
  <td class="numeric sum-cell">${num(f.litres)} L</td><td class="numeric sum-cell">${money(f.milk_amount)}</td>
  <td class="numeric payable-cell ${Number(f.payable)<0?'negative':''}">${money(f.payable)}</td></tr>`).join('');
 const mobile=visible.map(f=>`<div class="term-farmer" data-find="${clean((f.name+' '+f.code).toLowerCase())}">
  <button class="term-farmer-top" data-action="homeFarmer" data-id="${f.customer_id}"><span><b>${clean(f.name)}</b><small>${clean(f.code)} · ${num(f.litres)} L</small></span><strong class="${Number(f.payable)<0?'negative':'positive'}">${money(f.payable)}</strong><span class="chevron">›</span></button>
  <div class="day-chips">${days.filter(d=>Number(f.days[d].litres)>0).map(d=>`<button data-action="homeFarmer" data-id="${f.customer_id}" data-date="${d}" class="day-chip"><small>${clean(d.slice(8))}</small><b>${num(f.days[d].litres)} L</b></button>`).join('')||`<small>${t('या टर्ममध्ये दूध नाही','No milk this term')}</small>`}</div>
  <div class="term-farmer-meta"><span>${t('दूध रक्कम','Milk earnings')} ${money(f.milk_amount)}</span><span>${Number(f.payable)<0?t('पुढील बाकी','Carry forward'):t('आतापर्यंत देय','Payable so far')}</span></div></div>`).join('');
 $('#view').innerHTML=`<section class="hero compact-hero"><div><h2>${t('सध्याचा दूध हिशोब','Your term at a glance')}</h2><p>${t('प्रत्येक शेतकऱ्याची रोजची नोंद आणि आतापर्यंत देय रक्कम','Every farmer, every day, and the running payable amount')}</p></div>${btn('▤ '+t('फोटो स्कॅन करा','Scan a photo'),'scanGo','primary')}</section>
 ${termControls()}
 <div class="stats">${stat(t('या टर्मचे दूध','Term milk'),`${num(board.total_litres)} L`,`${board.start} — ${board.end}`,'◉')}
 ${stat(t('दूध रक्कम','Milk earnings'),money(board.total_milk_amount),t('मंजूर नोंदींची बेरीज','Approved milk credits'),'₹')}
 ${stat(t('शेतकऱ्यांना देय','Payable to farmers'),money(board.total_payable),t('कपातींनंतर अंदाज','After current deductions'),'↗')}
 ${stat(t('तपासणी बाकी','Needs review'),num(summary.pending_rows),t('स्कॅन नोंदी','Scanned entries'),'⚠')}</div>
 ${panel(t('शेतकऱ्यांचा रोजचा हिशोब','Daily term register'),`
  <div class="term-topline"><p class="small-muted">${t('दिवस निवडा → दूध नोंदी तपासा किंवा ✎ दुरुस्ती करा','Tap a day to inspect its milk entries and use ✎ to correct them')}</p><div class="actions">${btn('＋ '+t('दूध नोंद','Milk entry'),'manualMilk','small')}${btn(state.showAllFarmers?t('फक्त नोंदी असलेले','With entries only'):t('सर्व शेतकरी','All farmers'),'toggleAllFarmers','small')}</div></div>
  <input class="search term-search" id="termSearch" placeholder="${t('शेतकरी नाव / क्रमांक शोधा','Search farmer name / code')}" aria-label="Search term register">
  <div class="term-table desktop-table"><table><thead><tr><th class="sticky-farmer">${t('शेतकरी','Farmer')}</th>${days.map(d=>`<th title="${d}">${clean(d.slice(8))}</th>`).join('')}<th>${t('लिटर','Litres')}</th><th>${t('दूध रक्कम','Milk ₹')}</th><th>${t('देय ₹','Payable ₹')}</th></tr></thead><tbody>${gridRows||`<tr><td colspan="${days.length+4}">${t('या टर्ममध्ये नोंदी नाहीत','No entries in this term')}</td></tr>`}</tbody></table></div>
  <div class="term-mobile mobile-card-list">${mobile||empty(t('या टर्ममध्ये नोंदी नाहीत','No entries for this term'))}</div>
  <p class="small-muted term-footnote">${t('देय रक्कम = दूध रक्कम − पशुखाद्य − अॅडव्हान्स हप्ता − व्याज − अनामत − मागील बाकी. हा मसुदा आहे; अंतिम बिल वेगळे आहे.','Payable = milk earnings − feed − advance installment − interest − anamat − prior carry. Live estimate until billing is finalized.')}</p>
  ${board.finalized?pill(t('अंतिम बिल तयार','Finalized')):`${pill(t('सुधारणा केल्यावर आपोआप हिशोब बदलेल','Updates after corrections'),'gray')}`}`)}
 <div class="two">${panel(t('त्वरित कामे','Quick actions'),`<div class="action-grid">${actionTile('◉',t('दूध नोंदी','Milk entries'),t('तपशील व दुरुस्ती','Details and corrections'),'milk')}${actionTile('▣',t('पशुखाद्य','Cattle feed'),t('विक्री व साठा','Sales and stock'),'feed')}${actionTile('₹',t('१० दिवसांचे बिल','10-day billing'),t('हिशोब व बँक फाईल','Settlement and bank export'),'billing')}</div>`)}
 ${panel(t('लक्ष द्या','Attention'),`${summary.pending_rows?`<div class="alert warn">⚠ ${t('तपासणी बाकी','Pending review')} <b>${num(summary.pending_rows)}</b></div>`:`<div class="alert ok">✓ ${t('सर्व नोंदी तपासल्या','No review pending')}</div>`}<div class="alert">${t('कमी पशुखाद्य','Low stock')} <b>${num(summary.low_stock)}</b></div>${btn(t('स्कॅन तपासा','Review scans'),'scanGo')}`)}</div>`;
 const search=$('#termSearch');if(search)search.oninput=()=>{const q=search.value.trim().toLowerCase();$$('[data-find]').forEach(el=>{el.hidden=!el.dataset.find.includes(q)})};
}
function homeFarmer(el){const id=Number(el.dataset.id),date=el.dataset.date||'';
 const board=state.termBoard;const f=board?.farmers.find(r=>r.customer_id===id);if(!f)return;
 const rows=(state.milkRows||[]).filter(r=>r.customer_id===id&&(!date||r.date===date));
 showModal(`${clean(f.name)} · ${clean(f.code)}`,`<div class="stack">
  <div class="moneyline"><span>${t('दूध एकूण','Total milk')}</span><b>${num(f.litres)} L · ${money(f.milk_amount)}</b></div>
  <div class="moneyline total"><span>${t('आतापर्यंत देय','Payable so far')}</span><b>${money(f.payable)}</b></div>
  <h3>${date?clean(date):t('या टर्मच्या नोंदी','Entries in this term')}</h3>
  ${rows.length?rows.map(r=>`<div class="milk-detail-line"><div><b>${clean(r.date)} · ${clean(r.session||'')}</b><small>${num(r.quantity)} L · FAT ${num(r.fat)} · SNF ${num(r.snf)}</small></div><strong>${money(r.amount)}</strong><button class="iconbutton icon-edit" data-action="editHomeMilk" data-id="${r.id}" title="${t('दुरुस्ती','Correct entry')}" aria-label="${t('नोंद दुरुस्त करा','Correct entry')}">✎</button></div>`).join(''):empty(t('या दिवशी दूध नोंद नाही','No milk entries for this date'))}
 </div>`,btn('＋ '+t('दूध नोंद','Add milk'),'manualMilk','outline'));}
function editHomeMilk(el){state.editReturnPage='home';milkDetails(el.dataset.id)}

function fieldBadge(row,key){const conf=row.field_confidence?.[key];const value=key==='quantity_litres'?row.quantity:key==='fat_percent'?row.fat:key==='snf_percent'?row.snf:key==='quantity'?row.quantity:'';
 const corrected=row.corrected_by||row.field_values?.[key]?.corrected_value!==undefined&&row.field_values?.[key]?.corrected_value!==row.field_values?.[key]?.value;
 if(value===null||value===undefined||value==='')return `<span class="invalid">?</span>`;
 return !corrected&&conf!==undefined&&conf<.9?`<span class="${conf<.7?'invalid':'uncertain'}">${num(value)}</span>`:num(value)}
async function milkPage(){const d=await api(`/api/v2/milk?start=${state.start}&end=${state.end}`);state.milkRows=d.rows;
 const rows=d.rows.map(r=>`<tr class="clickable" data-action="milkDetail" data-id="${r.id}"><td class="namecell">${clean(r.code)} <span class="muted">${clean(r.name)}</span><small style="display:block">${clean(r.date)} · ${clean(r.session||'')}</small></td><td class="numeric">${num(r.quantity)} L</td><td>${money(r.rate)}</td><td class="numeric">${money(r.amount)}</td><td><span class="edit-glyph" aria-hidden="true">✎</span></td></tr>`).join('');
 $('#view').innerHTML=`<div class="sectionline"><div><p class="small-muted">${t('सध्याचा हिशोब आणि रोजची दूध नोंद','Daily approved milk records')}</p></div><div class="actions">${btn('＋ '+t('दूध भरा','Add milk'),'manualMilk','primary')}${btn('▤ '+t('स्कॅन','Scan'),'scanGo')}</div></div>
 ${termControls()}<div class="stats">${stat(t('एकूण दूध','Total milk'),num(d.litres)+' L',t('या टर्ममध्ये','In this term'),'◉')}${stat(t('एकूण रक्कम','Total amount'),money(d.amount),t('दर पत्रकानुसार','Using approved rate chart'),'₹')}</div>
 ${panel(t('दूध नोंदी','Milk records'),`<div class="desktop-table">${table([t('शेतकरी','Farmer'),t('लिटर','Litres'),t('दर / लि.','Rate / L'),t('रक्कम','Amount'),''],rows)}</div>
 <div class="mobile-card-list">${d.rows.map(r=>`<button class="record-card" data-action="milkDetail" data-id="${r.id}"><span><b>${clean(r.name)} · ${clean(r.code)}</b><small>${clean(r.date)} · ${clean(r.session||'')} · ${money(r.rate)}/L</small></span><span class="card-numbers"><strong>${num(r.quantity)} L</strong><strong>${money(r.amount)}</strong></span><span class="edit-glyph" aria-hidden="true">✎</span></button>`).join('')||empty()}</div>`)}`;
}
function milkDetails(id){const r=(state.milkRows||[]).find(x=>x.id===Number(id));if(!r)return;
 state.editTarget=r.id;state.editVersion=r.version;
 showModal(`${clean(r.name)} · ${clean(r.code)}`,`<div class="stack"><div class="notice">${t('दुरुस्ती केल्यावर दर आणि रक्कम सर्व्हरवर पुन्हा मोजली जाईल. अंतिम बिलातील नोंदी बदलता येत नाहीत.','Correct a milk entry before final billing. Server recalculates the rate and amount.')}</div>
 <div class="formgrid">${select('cmCustomer',t('शेतकरी','Farmer'),customerOptions(),r.customer_id)}${input('cmDate',t('तारीख','Date'),r.date,'date')}
 ${select('cmSession',t('सत्र','Session'),[['morning',t('सकाळ','Morning')],['evening',t('संध्याकाळ','Evening')]],r.session)}
 ${select('cmType',t('दूध','Milk type'),[['cow',t('गाय','Cow')],['buffalo',t('म्हैस','Buffalo')]],r.milk_type)}
 ${input('cmQuantity',t('दूध (लिटर)','Litres'),r.quantity,'number','min="0.001" step="0.001"')}
 ${input('cmFat','FAT %',r.fat,'number','min="0.01" step="0.01"')}
 ${input('cmSnf','SNF %',r.snf,'number','min="0.01" step="0.01"')}</div>
 <div id="cmPreview" class="notice">${t('सध्याची रक्कम','Current amount')}: <b>${money(r.amount)}</b> · ${money(r.rate)}/L</div>
 <small>${t('दुरुस्तीचा इतिहास जतन केला जातो.','Every correction is audited; previously posted credits get a separate adjustment.')}</small></div>`,btn(t('दुरुस्ती जतन करा','Save correction'),'saveMilkCorrection','primary'));
}
function correctionRequest(){return {customer_id:Number(value('cmCustomer')),collection_date:value('cmDate'),
 session:value('cmSession'),milk_type:value('cmType'),quantity:value('cmQuantity'),
 fat:value('cmFat'),snf:value('cmSnf')}}
let cmPreviewTimer=0,cmRequestId=0;
async function previewCorrectedMilk(){const data=correctionRequest();const seq=++cmRequestId;
 const panel=$('#cmPreview');if(!panel)return;panel.textContent=t('दर मोजला जात आहे…','Calculating rate…');
 if(!data.customer_id||!data.collection_date||Number(data.quantity)<=0||Number(data.fat)<=0||Number(data.snf)<=0)return;
 try{const result=await post('/api/milk-entries/calculate',data);
 if(seq!==cmRequestId||!$('#cmPreview'))return;
 $('#cmPreview').innerHTML=result.calculation_status==='CALCULATED'?`${t('नवीन दर','New rate')}: <b>${money(result.rate_per_litre)}</b> /L · ${t('रक्कम','Amount')}: <b>${money(result.total_amount)}</b>`:
 `<span class="invalid">${clean((result.warnings||[]).join(' · ')||t('दर उपलब्ध नाही','Rate unavailable'))}</span>`;
 }catch(e){if(seq===cmRequestId&&$('#cmPreview'))$('#cmPreview').textContent=e.message}}
async function saveMilkCorrection(){const body={...correctionRequest(),expected_version:state.editVersion};
 const res=await put(`/api/v2/milk/${state.editTarget}`,body);closeModal();toast(`${t('दुरुस्ती जतन झाली','Correction saved')} · ${money(res.amount)}`);const destination=state.editReturnPage||'milk';state.editReturnPage='milk';await go(destination)}
function manualMilkDialog(){showModal(t('नवीन दूध नोंद','Add milk record'),`<div class="formgrid">${select('mCustomer',t('शेतकरी','Farmer'),customerOptions())}${input('mDate',t('तारीख','Date'),today(),'date')}${select('mSession',t('सत्र','Session'),[['morning',t('सकाळ','Morning')],['evening',t('संध्याकाळ','Evening')]])}${select('mType',t('दूध','Milk type'),[['cow',t('गाय','Cow')],['buffalo',t('म्हैस','Buffalo')]])}${input('mQuantity',t('दूध (लिटर)','Litres'),'','number','step="0.001" min="0.001"')}${input('mFat','FAT %','','number','step="0.01" min="0.01"')}${input('mSnf','SNF %','','number','step="0.01" min="0.01"')}</div><div class="notice" style="margin-top:12px">${t('दर आणि रक्कम सर्व्हरवरील दरपत्रकावरून मोजली जाईल.','The backend calculates rate and amount from the saved chart.')}</div>`,btn(t('नोंद जतन करा','Save milk'),'saveMilk','primary'))}
async function saveMilk(){const body={customer_id:Number(value('mCustomer')),collection_date:value('mDate'),session:value('mSession'),milk_type:value('mType'),quantity:value('mQuantity'),fat:value('mFat'),snf:value('mSnf')};
 const d=await post('/api/v2/milk/manual',body);closeModal();toast(`${t('दूध नोंद जतन झाली','Milk saved')} · ${money(d.amount)}`);await go('milk')}
async function scanPage(){const recent=await api('/api/v2/scans');
 const types=[['auto',t('आपोआप ओळखा','Detect automatically')],['milk',t('दूध नोंद','Milk register')],['feed',t('पशुखाद्य विक्री','Feed sales')],['rate_chart',t('दूध दर पत्रक','Rate chart')]];
 $('#view').innerHTML=`<section class="hero"><div><h2>${t('फोटो घ्या. उरलेले DairyAI करेल.','Snap it. DairyAI will read it.')}</h2><p>${t('मजकूर वाचल्यानंतर तुम्ही सर्व माहिती तपासू शकता. मंजुरीशिवाय खात्यात बदल होत नाही.','Review every entry before anything is posted.')}</p></div><span style="font-size:46px">▤</span></section>
 <section class="panel narrow"><div class="panelhead"><h2>${t('AI स्मार्ट स्कॅन','AI Smart Scan')}</h2>${pill('Gemini Vision')}</div>
 <div class="filezone"><span class="symbol">📷</span><b>${t('फोटो घ्या किंवा निवडा','Take or upload a photo')}</b><p>${t('दूध · पशुखाद्य · दर पत्रक','Milk · Feed · Rate chart')}</p>
 <input id="scanCamera" type="file" accept="image/*" capture="environment" class="visually-hidden-input" aria-label="Take a register photo">
 <input id="scanFile" type="file" accept="image/jpeg,image/png" class="visually-hidden-input" aria-label="Choose an image from device">
 <div class="actions upload-actions">${btn('📷 '+t('फोटो काढा','Take photo'),'takeScanPhoto','primary')}${btn('▣ '+t('फोटो निवडा','Upload photo'),'selectScanPhoto')}</div>
 <div id="scanFileSummary" class="small-muted" role="status">${t('फोटो निवडलेला नाही','No photo selected')}</div><img id="scanPhotoPreview" class="scan-preview" hidden alt="Selected register photograph"></div>
 <div class="chips" id="scanTypes">${types.map(([k,n])=>`<button class="chip ${k===state.scanType?'active':''}" data-action="scanType" data-value="${k}">${n}</button>`).join('')}</div>
 <div class="actions">${btn('✦ '+t('फोटो वाचा','Extract entries'),'scanUpload','primary')}</div>
 <div class="notice" style="margin-top:14px">${t('AI चुकू शकतो. पिवळ्या/लाल नोंदी तपासून मगच मंजूर करा.','AI can misread handwriting. Check highlighted cells before approval.')}</div></section>
 ${panel(t('मागील स्कॅन','Recent scans'),table([t('तारीख','Date'),t('प्रकार','Type'),t('नोंदी','Rows'),t('स्थिती','Status'),''],recent.map(r=>`<tr class="clickable" data-action="openBatch" data-id="${r.id}"><td>${clean((r.date||'').slice(0,10))}</td><td>${clean(kindLabel(r.kind))}</td><td>${num(r.total_rows)}</td><td>${pill(r.pending_rows?t('तपासा','Review'):t('पूर्ण','Done'),r.pending_rows?'warn':'')}</td><td>›</td></tr>`).join('')))}`;
 const onfile=evt=>selectScanImage(evt.target.files?.[0]);$('#scanCamera').addEventListener('change',onfile);$('#scanFile').addEventListener('change',onfile);
 if(state.scan)renderScanReview();
}
function kindLabel(kind){return ({milk:t('दूध','Milk'),MILK_REGISTER:t('दूध','Milk'),feed:t('पशुखाद्य विक्री','Feed sales'),CATTLE_FEED_PURCHASE:t('पशुखाद्य विक्री','Feed sales'),rate_chart:t('दर पत्रक','Rate chart'),RATE_CHART:t('दर पत्रक','Rate chart')})[kind]||kind}
function selectScanImage(file){if(!file)return;if(!['image/jpeg','image/png'].includes(file.type)){toast(t('JPG किंवा PNG फोटो निवडा','Choose a JPG or PNG photo'),true);return}state.scanSelectedFile=file;const info=$('#scanFileSummary');if(info)info.textContent=file.name+' · '+(file.size/1024/1024).toFixed(1)+' MB';const img=$('#scanPhotoPreview');if(img){if(img.dataset.url)URL.revokeObjectURL(img.dataset.url);const url=URL.createObjectURL(file);img.dataset.url=url;img.src=url;img.hidden=false}}
async function scanUpload(){const file=state.scanSelectedFile||$('#scanFile')?.files[0]||$('#scanCamera')?.files[0];if(!file)throw Error(t('फोटो निवडा','Choose a photo'));
 const fd=new FormData();fd.append('file',file);const kind=state.scanType;
 $('#view').innerHTML=loading();try{const result=await api(`/api/scans?kind=${encodeURIComponent(kind)}`,'POST',fd);
 state.scan=await api(`/api/scans/${result.batch_id}`);state.scanSelectedFile=null;state.scan.provider=result.provider;state.scan.model=result.model;state.scanType='auto';if(state.scan.register_type==='CATTLE_FEED_PURCHASE')await refreshProducts();await go('scan');
 }catch(e){await go('scan');throw e}}
async function openBatch(id){state.scan=await api(`/api/scans/${id}`);if(state.scan.register_type==='CATTLE_FEED_PURCHASE')await refreshProducts();await go('scan')}
function scanReviewRow(r,type){const approved=r.approved,low=(Number(r.confidence||0)<.9)||(r.calculation_status && r.calculation_status!=='CALCULATED');
 let middle;if(type==='milk')middle=`<td>${fieldBadge(r,'quantity_litres')} L</td><td>${r.rate_per_litre!==null?money(r.rate_per_litre):'<span class="invalid">—</span>'}</td><td class="numeric">${r.calculated_amount!==null?money(r.calculated_amount):'—'}</td>`;
 else middle=`<td>${clean(jsonField(r,'product_code')||jsonField(r,'product_name')||'—')}</td><td>${fieldBadge(r,'quantity')}</td><td>${r.amount?money(r.amount):t('मंजुरीनंतर','After approval')}</td>`;
 return `<tr class="clickable" data-action="reviewRow" data-id="${r.id}"><td class="namecell">${clean(r.extracted_code||r.extracted_name||'—')}</td>${middle}<td>${pill(approved?t('मंजूर','Approved'):low?t('तपासा','Review'):t('योग्य','Ready'),approved?'':low?'warn':'')}</td><td><span class="edit-glyph" aria-hidden="true">✎</span></td></tr>`}
function renderScanReview(){const batch=state.scan;if(!batch)return;let old=$('#scanReview');if(old)old.remove();
 const type=batch.register_type==='MILK_REGISTER'?'milk':batch.register_type==='CATTLE_FEED_PURCHASE'?'feed':batch.register_type==='RATE_CHART'?'rate_chart':'other';
 let content='';if(type==='rate_chart'){content=chartImportReview(batch)}else{
 const rows=batch.rows.filter(r=>r.approval_status!=='deleted');const needs=rows.filter(r=>!r.approved&&Number(r.confidence||0)<.9);
 const headers=type==='milk'?[t('शेतकरी','Farmer'),t('लिटर','Litres'),t('दर','Rate'),t('रक्कम','Amount'),t('स्थिती','Status'),'']:[t('शेतकरी','Farmer'),t('पशुखाद्य','Product'),t('प्रमाण','Quantity'),t('रक्कम','Amount'),t('स्थिती','Status'),''];
 content=`<div class="stats">${stat(t('एकूण','Total'),num(rows.length),'','▤')}${stat(t('तपासा','Review'),num(needs.length),'','⚠')}${type==='milk'?stat(t('एकूण लिटर','Litres'),num(batch.summary?.total?.litres)+' L','','◉'):''}</div>
 <div class="desktop-table">${table(headers,rows.map(r=>scanReviewRow(r,type)).join(''))}</div>
 <div class="mobile-card-list">${rows.map(r=>`<button class="record-card ${!r.approved&&Number(r.confidence||0)<.9?'needs-review':''}" data-action="reviewRow" data-id="${r.id}"><span><b>${clean(r.extracted_code||r.extracted_name||'—')}</b><small>${type==='milk'?`${t('दर','Rate')} ${r.rate_per_litre!==null?money(r.rate_per_litre):'—'}`:clean(jsonField(r,'product_code')||jsonField(r,'product_name')||'—')}</small></span><span class="card-numbers"><strong>${fieldBadge(r,type==='milk'?'quantity_litres':'quantity')} ${type==='milk'?'L':''}</strong><strong>${type==='milk'?(r.calculated_amount!==null?money(r.calculated_amount):'—'):(r.amount?money(r.amount):'')}</strong></span><span class="edit-glyph" aria-hidden="true">✎</span></button>`).join('')||empty()}</div>
 <div class="actions" style="margin-top:16px">${btn('＋ '+t('नोंद जोडा','Add row'),'addScanRow')}${btn('✓ '+t('योग्य नोंदी मंजूर करा','Approve eligible'),'approveScan','primary')}</div>
 <div class="notice" style="margin-top:14px">${t('प्रत्येक ओळीवर टॅप करून बदल करता येतात. अस्पष्ट मूल्ये पिवळ्या/लाल रंगात.','Tap a row to correct it. Uncertain fields are colored.')}</div>`;
 }
 const review=`<section id="scanReview" class="panel"><div class="panelhead"><div><h2>${t('स्कॅन तपासणी','Scan review')} · ${kindLabel(batch.kind)}</h2><div class="sub">${batch.provider||batch.structured_extraction?.provider||'AI'} · #${batch.batch_id}</div></div>${pill(batch.status==='approved'?t('पूर्ण','Done'):t('मसुदा','Draft'),batch.status==='approved'?'':'warn')}</div>${content}
 <details style="margin-top:19px"><summary class="small-muted" style="cursor:pointer">${t('मूळ फोटो पहा','View original photo')}</summary><img class="photo" loading="lazy" src="/api/scans/${batch.batch_id}/image?requires_auth" id="scanProtectedPhoto" alt="Original register photograph"><div class="small-muted">${t('फोटो फक्त लॉग इन केलेल्या वापरकर्त्यास दिसतो','Photo is private to this dairy')}</div></details></section>`;
 const parent=$('#view');parent.insertAdjacentHTML('afterbegin',review);loadProtectedPhoto(batch.batch_id);
}
async function loadProtectedPhoto(id){const el=$('#scanProtectedPhoto');if(!el)return;try{const res=await fetch(`/api/scans/${id}/image`,{headers:{Authorization:`Bearer ${state.token}`}});if(!res.ok)return;const blob=await res.blob();const url=URL.createObjectURL(blob);el.src=url;el.onload=()=>URL.revokeObjectURL(url)}catch{}}
function scanRowDialog(id){const r=state.scan?.rows.find(r=>r.id===Number(id));if(!r)return;
 const type=state.scan.register_type;
 let fields=`<div class="formgrid">${select('reviewCustomer',t('शेतकरी','Farmer'),customerOptions(),r.customer_id)}${input('reviewQty',t('प्रमाण / लिटर','Quantity / Litres'),r.quantity??'','number','step="0.001"')}`;
 if(type==='MILK_REGISTER')fields+=`${input('reviewFat','FAT %',r.fat??'','number','step="0.01"')}${input('reviewSnf','SNF %',r.snf??'','number','step="0.01"')}${input('reviewDate',t('तारीख','Date'),r.collection_date||today(),'date')}${select('reviewSession',t('सत्र','Session'),[['',t('निवडा','Select')],['morning',t('सकाळ','Morning')],['evening',t('संध्याकाळ','Evening')]],r.session||'')}${select('reviewMilkType',t('दूध','Milk'),[['',t('निवडा','Select')],['cow',t('गाय','Cow')],['buffalo',t('म्हैस','Buffalo')]],r.milk_type||'')}`;
 else {const code=jsonField(r,'product_code'),name=jsonField(r,'product_name');
 const match=state.products.find(p=>p.product_code.toLowerCase()===String(code).toLowerCase()||p.product_name.toLowerCase()===String(name).toLowerCase());
 fields+=select('reviewProduct',t('पशुखाद्य निवडा','Select actual feed'),productOptions(),match?.id||'');}
 fields+='</div>';
 const amounts=type==='MILK_REGISTER'?`<div class="moneyline"><span>${t('दर / लिटर','Rate / L')}</span><b>${r.rate_per_litre!==null?money(r.rate_per_litre):'—'}</b></div><div class="moneyline total"><span>${t('रक्कम','Amount')}</span><span>${r.calculated_amount!==null?money(r.calculated_amount):'—'}</span></div>`:'';
 const notices=(r.warnings||[]).slice(0,3).map(w=>`<small style="display:block;color:var(--ambertext)">⚠ ${clean(w)}</small>`).join('');
 state.editTarget=r.id;showModal(`${clean(r.extracted_code||r.extracted_name)} · ${t('तपशील','Details')}`,`<div class="stack">${r.approved?'<div class="notice">Approved record (read only).</div>':''}${fields}${amounts}${notices}</div>`,r.approved?'':btn(t('जतन करा आणि दर पुन्हा मोजा','Save and recalculate'),'saveScanRow','primary'))}
async function saveScanRow(){const r=state.scan.rows.find(x=>x.id===state.editTarget);if(!r)return;
 const body={expected_version:r.version,customer_id:Number(value('reviewCustomer'))||null,quantity:Number(value('reviewQty'))||null};
 if(state.scan.register_type==='MILK_REGISTER')Object.assign(body,{fat:Number(value('reviewFat'))||null,snf:Number(value('reviewSnf'))||null,
  collection_date:value('reviewDate')||null,session:value('reviewSession')||null,milk_type:value('reviewMilkType')||null});
 else {const product=state.products.find(p=>p.id===Number(value('reviewProduct')));
 if(!product)throw Error(t('योग्य पशुखाद्य निवडा','Choose a registered feed product'));
 Object.assign(body,{product_code:product.product_code,product_name:product.product_name});}
 await put(`/api/scans/rows/${r.id}`,body);closeModal();state.scan=await api(`/api/scans/${state.scan.batch_id}`);await go('scan');toast(t('नोंद जतन झाली','Entry saved'))}
function addScanRowDialog(){const b=state.scan;if(!b||b.register_type==='RATE_CHART')return;
 const milk=b.register_type==='MILK_REGISTER';showModal(t('हाताने नोंद जोडा','Add missing row'),`<div class="formgrid">${select('newRowCustomer',t('शेतकरी','Farmer'),customerOptions())}${input('newRowQty',t('प्रमाण','Quantity'),'','number','step="0.001"')}${milk?`${input('newRowFat','FAT %','','number','step="0.01"')}${input('newRowSnf','SNF %','','number','step="0.01"')}${select('newRowType',t('दूध','Milk type'),[['cow',t('गाय','Cow')],['buffalo',t('म्हैस','Buffalo')]])}${select('newRowSession',t('सत्र','Session'),[['morning',t('सकाळ','Morning')],['evening',t('संध्याकाळ','Evening')]])}`:`${select('newRowProduct',t('पशुखाद्य','Product'),productOptions())}`}${input('newRowDate',t('तारीख','Date'),today(),'date')}</div>`,btn(t('नोंद जोडा','Add entry'),'saveNewRow','primary'))}
async function saveNewRow(){const b=state.scan,milk=b.register_type==='MILK_REGISTER';let body={customer_id:Number(value('newRowCustomer')),quantity:Number(value('newRowQty')),
 collection_date:value('newRowDate')};
 if(milk)Object.assign(body,{fat:Number(value('newRowFat')),snf:Number(value('newRowSnf')),milk_type:value('newRowType'),session:value('newRowSession')});
 else{const product=state.products.find(p=>p.id===Number(value('newRowProduct')));if(!product)throw Error(t('पशुखाद्य निवडा','Choose product'));Object.assign(body,{product_code:product.product_code,product_name:product.product_name})}
 await post(`/api/scans/${b.batch_id}/rows`,body);closeModal();state.scan=await api(`/api/scans/${b.batch_id}`);await go('scan');toast(t('नोंद जोडली','Row added'))}
async function approveScan(){const b=state.scan;if(!b)return;const selected=b.rows.filter(r=>!r.approved&&r.approval_status!=='deleted'&&r.customer_id&&Number(r.quantity)>0&&
   (b.register_type!=='MILK_REGISTER'||r.calculation_status==='CALCULATED'));
 if(!selected.length)throw Error(t('मंजुरीसाठी योग्य नोंदी नाहीत','No eligible rows to approve'));
 if(!confirm(t(`${selected.length} नोंदी मंजूर करून खात्यात जतन करायच्या?`,`Approve ${selected.length} rows and post to ledger?`)))return;
 let result;if(b.register_type==='MILK_REGISTER')result=await post(`/api/scans/${b.batch_id}/approve`,{row_ids:selected.map(r=>r.id)});
 else if(b.register_type==='CATTLE_FEED_PURCHASE')result=await post(`/api/v2/feed/scans/${b.batch_id}/approve`,{row_ids:selected.map(r=>r.id),sale_date:today()});
 else throw Error('Unsupported batch kind');
 state.scan=await api(`/api/scans/${b.batch_id}`);await go('scan');toast(result.errors?.length?result.errors.join('; '):t('नोंदी मंजूर झाल्या','Entries approved'));
}
async function feedPage(){await refreshProducts();const d=await api('/api/feed/dashboard');let section='';
 const tabs=`<div class="tabs">${[['stock',t('साठा','Stock')],['sales',t('विक्री','Sales')],['history',t('इतिहास','History')]].map(([k,n])=>`<button class="tab ${state.feedTab===k?'active':''}" data-action="feedTab" data-value="${k}">${n}</button>`).join('')}</div>`;
 if(state.feedTab==='stock'){
  section=`<div class="sectionline"><div class="small-muted">${t('उपलब्ध पशुखाद्य आणि किंमती','Available products and prices')}</div><div class="actions">${btn('＋ '+t('साठा भरा','Stock inward'),'bulkStock','primary')}${btn(t('नवीन प्रकार','New product'),'newProduct')}</div></div>
  <div class="stats">${stat(t('प्रकार','Products'),num(d.products.length),'','▣')}${stat(t('साठ्याची किंमत','Stock value'),money(d.total_inventory_value),t('सरासरी खरेदी खर्च','Average cost'),'₹')}${stat(t('कमी साठा','Low stock'),num(d.low_stock_alerts.length),'','⚠')}</div>
  ${table([t('पशुखाद्य','Product'),t('उपलब्ध','Available'),t('खरेदी दर','Buy rate'),t('विक्री दर','Sell rate'),t('स्थिती','Status')],d.products.map(p=>`<tr><td class="namecell">${clean(p.product_name)}<small style="display:block">${clean(p.product_code)}</small></td><td>${num(p.available_quantity)} ${clean(p.unit)}</td><td>${money(p.purchase_price)}</td><td>${money(p.selling_price)}</td><td>${p.low_stock?pill(t('कमी','Low'),'warn'):pill(t('ठीक','OK'))}</td></tr>`).join(''))}`;
 } else if(state.feedTab==='sales'){
  const sales=await api('/api/feed/sales?limit=40');
  section=`<div class="sectionline"><span class="small-muted">${t('शेतकऱ्याला पशुखाद्य दिल्यावर खात्यात कपात होईल','Sales debit farmer accounts after approval')}</span><div class="actions">${btn('▤ '+t('विक्री वही स्कॅन','Scan sales register'),'feedScan','primary')}${btn('＋ '+t('विक्री भरा','Manual sale'),'manualSale')}</div></div>
 ${table([t('तारीख','Date'),t('शेतकरी','Farmer'),t('पशुखाद्य','Product'),t('रक्कम','Amount')],sales.map(s=>`<tr><td>${clean(s.sale_date)}</td><td class="namecell">${clean(s.customer_name)}</td><td>${s.items.map(i=>`${clean(i.product_name)} × ${num(i.quantity)}`).join(', ')}</td><td class="numeric">${money(s.total_amount)}</td></tr>`).join(''))}`;
 }else{
  const history=await api('/api/feed/purchases?limit=50');
  section=table([t('तारीख','Date'),t('पशुखाद्य','Product'),t('प्रमाण','Quantity'),t('खरेदी दर','Buy rate')],history.map(p=>`<tr><td>${clean(p.purchase_date)}</td><td>${clean(p.product_name)}</td><td>${num(p.quantity)} ${clean(p.unit)}</td><td>${money(p.purchase_rate)}</td></tr>`).join(''));
 }
 $('#view').innerHTML=`<div class="sectionline"><p class="small-muted">${t('साठा आणि विक्रीसाठी स्वतंत्र सोपी स्क्रीन','Stock inward and farmer sales are separate tasks')}</p></div>${tabs}${panel(state.feedTab==='stock'?t('उपलब्ध पशुखाद्य','Available stock'):state.feedTab==='sales'?t('पशुखाद्य विक्री','Feed sales'):t('साठा आवक इतिहास','Stock inward history'),section)}`;
}
function newProductDialog(){showModal(t('नवीन पशुखाद्य','New feed product'),`<div class="formgrid">${input('fpName',t('नाव','Name'),'','','required')}${input('fpCode',t('प्रकार / कोड','Product code'),'','','required')}${select('fpUnit',t('एकक','Unit'),[['BAG',t('गोणी','Bag')],['KG',t('किलो','Kg')],['TON',t('टन','Ton')],['LITRE',t('लिटर','Litre')]])}${input('fpCategory',t('प्रकार (ऐच्छिक)','Category (optional)'))}${input('fpPurchase',t('खरेदी दर','Purchase price'),'0','number','min="0" step="0.01"')}${input('fpSell',t('विक्री दर','Selling price'),'0','number','min="0" step="0.01"')}${input('fpLow',t('कमी साठ्याची सूचना','Low stock threshold'),'0','number','min="0" step="0.001"')}</div>`,btn(t('जतन करा','Save product'),'saveProduct','primary'))}
async function saveProduct(){await post('/api/feed/products',{product_name:value('fpName'),product_code:value('fpCode'),category:value('fpCategory'),unit:value('fpUnit'),purchase_price:value('fpPurchase'),selling_price:value('fpSell'),low_stock_threshold:value('fpLow')});closeModal();toast(t('पशुखाद्य जतन झाले','Product added'));await go('feed')}
function stockRow(){return `<div class="bulk-row formgrid" style="border-bottom:1px solid var(--line);padding:12px 0">${select('','पशुखाद्य',productOptions()).replace('<select id=""','<select class="stock-product"')}${input('','प्रमाण','','number','min="0.001" step="0.001"').replace('id=""','class="stock-quantity"')}${input('','खरेदी दर','','number','min="0" step="0.01"').replace('id=""','class="stock-buy"')}${input('','विक्री दर (नवीन)','','number','min="0" step="0.01"').replace('id=""','class="stock-sell"')}<button class="btn danger" type="button" data-action="removeStockRow">× ${t('काढा','Remove')}</button></div>`}
function bulkStockDialog(){showModal(t('नवीन साठा आवक','Bulk incoming stock'),`${input('stockDate',t('खरेदी तारीख','Purchase date'),today(),'date')}<div class="notice" style="margin:12px 0">${t('एकाच वेळी अनेक पशुखाद्य प्रकार भरा.','Add several products together. This is separate from customer sales.')}</div><div id="stockRows">${stockRow()}</div>${btn('＋ '+t('आणखी ओळ','Add row'),'addStockRow')}`,btn(t('सर्व साठा जतन करा','Save all stock'),'saveBulkStock','primary'))}
async function saveBulkStock(){const rows=$$('.bulk-row').map(row=>({product_id:Number(row.querySelector('.stock-product').value),quantity:row.querySelector('.stock-quantity').value,purchase_rate:row.querySelector('.stock-buy').value,
 selling_price:row.querySelector('.stock-sell').value===''?null:row.querySelector('.stock-sell').value}));
 if(rows.some(r=>!r.product_id||Number(r.quantity)<=0||Number(r.purchase_rate)<0))throw Error(t('सर्व उत्पादन, प्रमाण आणि दर भरा','Complete all product, quantity and price rows'));
 await post('/api/v2/feed/stock/bulk',{purchase_date:value('stockDate'),idempotency_key:crypto.randomUUID(),items:rows});closeModal();toast(t('साठा जतन झाला','Stock saved'));await go('feed')}
function manualSaleDialog(){showModal(t('शेतकऱ्याला पशुखाद्य विक्री','Sell feed to a farmer'),`<div class="formgrid">${select('saleCustomer',t('शेतकरी','Farmer'),customerOptions())}${select('saleProduct',t('पशुखाद्य','Feed'),productOptions())}${input('saleQty',t('प्रमाण','Quantity'),'','number','min="0.001" step="0.001"')}${input('saleDate',t('तारीख','Date'),today(),'date')}</div><div class="notice" style="margin-top:15px">${t('नोंद जतन झाल्यावर साठा कमी होईल आणि शेतकऱ्याच्या खात्यात विक्री दराने कपात होईल.','Saving reduces stock and debits the farmer at the saved selling price.')}</div>`,btn(t('विक्री जतन करा','Save sale'),'saveSale','primary'))}
async function saveSale(){const product=state.products.find(p=>p.id===Number(value('saleProduct')));if(!product)throw Error(t('पशुखाद्य निवडा','Select product'));
 if(!confirm(`${t('रक्कम','Amount')}: ${money(Number(value('saleQty'))*Number(product.selling_price))}. ${t('खात्यात कपात करायची?','Post to farmer account?')}`))return;
 await post('/api/feed/sales',{customer_id:Number(value('saleCustomer')),sale_date:value('saleDate'),idempotency_key:crypto.randomUUID(),items:[{product_id:product.id,quantity:value('saleQty')}]});
 closeModal();state.feedTab='sales';toast(t('विक्री जतन झाली','Sale posted'));await go('feed')}
function rateMatrix(chart){const entries=chart?.entries||[];
 if(chart?.chart_type==='FIXED_RATE')return `<div class="notice">${t('निश्चित दर','Fixed rate')} · ${money(chart.fixed_rate)}</div>`;
 const fats=[...new Set(entries.map(e=>String(e.fat_value)).filter(x=>x!=='null'))].sort((a,b)=>Number(a)-Number(b));
 const snfs=chart?.chart_type==='FAT_ONLY'?[]:[...new Set(entries.map(e=>String(e.snf_value)).filter(x=>x!=='null'))].sort((a,b)=>Number(a)-Number(b));
 const cols=snfs.length?snfs:['—'];
 return `<div class="matrixwrap"><table class="matrix"><thead><tr><th>FAT / SNF</th>${cols.map(x=>`<th>${clean(x)}</th>`).join('')}</tr></thead><tbody>
 ${fats.map(f=>`<tr><th>${clean(f)}</th>${cols.map(s=>{const v=entries.find(e=>String(e.fat_value)===f&&(s==='—'||String(e.snf_value)===s));return `<td>${v?money(v.rate):'—'}</td>`}).join('')}</tr>`).join('')}</tbody></table></div>`;
}
async function ratesPage(){const charts=await api(`/api/rate-charts?milk_type=${state.rateMilk}`);state.rateCharts=charts;
 const active=charts.find(c=>c.status==='ACTIVE');state.currentChart=active||null;
 const versions=charts.map(c=>`<div class="alert"><span><b>${clean(c.name)}</b><small style="display:block">v${c.version} · ${clean(c.effective_from)} · ${clean(c.match_policy)}</small></span><div class="actions">${pill(c.status,c.status==='DRAFT'?'warn':'')}${c.status==='DRAFT'?btn(t('सक्रिय करा','Activate'),'activateChart','small',`data-id="${c.id}"`):''}</div></div>`).join('');
 $('#view').innerHTML=`<div class="sectionline"><p class="small-muted">${t('दरपत्रक थेट पाहा. बदलासाठी फोटो वापरा किंवा तक्ता संपादित करा.','View the rates directly; change by photo or edit the table.')}</p><div class="actions">${btn('▤ '+t('नवीन दरपत्रक स्कॅन','Scan chart'),'chartScan','primary')}${btn('✎','editChart','icon-edit',`aria-label="${t('तक्ता संपादित करा','Edit chart')}" title="${t('तक्ता संपादित करा','Edit chart')}"`)}</div></div>
 <div class="tabs">${[['cow',t('गाय दूध','Cow milk')],['buffalo',t('म्हैस दूध','Buffalo milk')]].map(([v,l])=>`<button class="tab ${state.rateMilk===v?'active':''}" data-action="rateMilk" data-value="${v}">${l}</button>`).join('')}</div>
 ${panel(t('सध्याचे दूध दर पत्रक','Active milk rate chart'),active?`${rateMatrix(active)}<div class="moneyline"><span>${t('लागू तारीख','Effective from')}</span><b>${clean(active.effective_from)}</b></div><div class="small-muted">v${active.version} · ${clean(active.match_policy)}</div>`:`<div class="empty">${t('सक्रिय दरपत्रक नाही. दरपत्रकाचा फोटो स्कॅन करा.','No active rate chart. Scan the real dairy chart to begin.')}</div>`)}
 ${panel(t('दर मोजून पाहा','Test rate'),`<div class="formgrid">${input('rateDate',t('दूध तारीख','Date'),today(),'date')}${input('rateLitres',t('लिटर','Litres'),'5','number','min="0.001" step="0.001"')}${input('rateFat','FAT %','','number','step="0.01"')}${input('rateSnf','SNF %','','number','step="0.01"')}</div><div class="actions" style="margin-top:12px">${btn(t('दर मोजा','Calculate'),'previewRate','primary')}</div><div id="rateResult" style="margin-top:10px"></div>`)}
 <details class="panel"><summary style="cursor:pointer;font-weight:750">${t('आधीची दरपत्रके आणि मसुदे','Previous versions and drafts')} (${charts.length})</summary><div style="margin-top:14px">${versions||empty()}</div></details>`;
}
function readVal(x){const v=x?.value;return v===null||v===undefined?'':String(v)}
function chartFromScan(batch){const s=batch.structured_extraction||{};
 const fats=(s.fat_values||[]).map(readVal).filter(Boolean);
 const snfs=(s.snf_values||[]).map(readVal).filter(Boolean);
 const cells=(s.matrix_cells||[]).map(c=>({fat_value:readVal(c.fat_value),snf_value:readVal(c.snf_value),rate:readVal(c.rate),confidence:c.rate?.confidence??0}));
 return {fats:[...new Set(fats.length?fats:cells.map(c=>c.fat_value).filter(Boolean))].sort((a,b)=>Number(a)-Number(b)),
         snfs:[...new Set(snfs.length?snfs:cells.map(c=>c.snf_value).filter(Boolean))].sort((a,b)=>Number(a)-Number(b)),
         cells,milk_type:['cow','buffalo'].includes(readVal(s.milk_type).toLowerCase())?readVal(s.milk_type).toLowerCase():state.rateMilk,
         effective_from:readVal(s.effective_date)||today(), name:`${t('फोटोवरून दरपत्रक','Scanned chart')} ${today()}`};
}
function chartImportReview(batch){state.chartDraft=chartFromScan(batch);
 return `<div class="notice warn" style="margin-bottom:14px">${t('AI ने वाचलेला तक्ता तपासा. रिकामे/पिवळे दर भरा. मंजुरीपूर्वी काहीही बदलणार नाही.','Review the AI matrix and correct missing rates before saving.')}</div>${chartEditorHtml(state.chartDraft)}`}
function chartEditorHtml(data){const fats=data.fats||[],snfs=data.snfs||[];
 const lookup=(f,s)=>data.cells?.find(c=>String(c.fat_value)===String(f)&&String(c.snf_value)===String(s));
 return `<div class="formgrid">${input('newChartName',t('दरपत्रक नाव','Chart name'),data.name||'Milk rate chart')}${select('newChartType',t('दुधाचा प्रकार','Milk type'),[['cow',t('गाय','Cow')],['buffalo',t('म्हैस','Buffalo')]],data.milk_type||'cow')}${input('newChartDate',t('लागू तारीख','Effective from'),data.effective_from||today(),'date')}${select('newChartPolicy',t('मधल्या मूल्यांसाठी नियम','Between-values policy'),[['EXACT_MATCH',t('जुळणारा दरच','Exact match')],['NEAREST_LOWER',t('खालचा दर','Nearest lower')],['NEAREST',t('जवळचा दर','Nearest')],['BILINEAR_INTERPOLATION',t('गणिती सरासरी','Interpolate')]],data.match_policy||'EXACT_MATCH')}</div>
 <details style="margin:15px 0"><summary class="small-muted" style="cursor:pointer">${t('FAT / SNF ओळी व स्तंभ बदला','Change FAT/SNF rows and columns')}</summary><div class="formgrid" style="margin-top:10px">${input('chartFats','FAT %',fats.join(', '))}${input('chartSnfs','SNF %',snfs.join(', '))}</div><button class="btn small outline" style="margin-top:8px" data-action="rebuildGrid">${t('तक्ता बदला','Update grid')}</button></details>
 <div class="matrixwrap" id="chartEditMatrix"><table class="matrix"><thead><tr><th>FAT / SNF</th>${snfs.map(s=>`<th>${clean(s)}</th>`).join('')}</tr></thead><tbody>${fats.map(f=>`<tr><th>${clean(f)}</th>${snfs.map(s=>{const c=lookup(f,s),v=c?.rate||'';return `<td><input class="matrix-rate ${!v||c?.confidence<.7?'danger-cell':''}" type="number" step="0.0001" min="0" data-fat="${clean(f)}" data-snf="${clean(s)}" value="${clean(v)}" aria-label="FAT ${clean(f)}, SNF ${clean(s)}"></td>`}).join('')}</tr>`).join('')}</tbody></table></div>
 <div class="actions" style="margin-top:17px">${btn('✓ '+t('दरपत्रक मसुदा जतन करा','Save chart draft'),'saveChartDraft','primary')}</div>
 <div class="small-muted" style="margin-top:10px">${t('दरपत्रक जतन केल्यानंतर दर पत्रक टॅबमध्ये सक्रिय करा.','After saving, activate the new chart version from Rate Chart.')}</div>`;
}
function editChartDialog(){const c=state.currentChart;const data=c?{fats:[...new Set(c.entries.map(e=>String(e.fat_value)))].sort((a,b)=>Number(a)-Number(b)),
 snfs:[...new Set(c.entries.map(e=>String(e.snf_value)))].sort((a,b)=>Number(a)-Number(b)),
 cells:c.entries.map(e=>({...e,confidence:1})),name:`${c.name} · ${t('नवीन आवृत्ती','New version')}`,milk_type:c.milk_type,
 effective_from:today(),match_policy:c.match_policy}:{fats:['3.5','3.6','3.7'],snfs:['8.4','8.5','8.6'],cells:[],name:'Milk rate chart',milk_type:state.rateMilk,effective_from:today()};
 state.chartDraft=data;showModal(t('दरपत्रक संपादित करा','Edit rate chart'),chartEditorHtml(data));}
function rebuildGrid(){const d=state.chartDraft||{};if(!d)return;
 d.cells=$$('.matrix-rate').map(e=>({fat_value:e.dataset.fat,snf_value:e.dataset.snf,rate:e.value,confidence:1}));
 d.fats=[...new Set(value('chartFats').split(',').map(x=>x.trim()).filter(Boolean))];
 d.snfs=[...new Set(value('chartSnfs').split(',').map(x=>x.trim()).filter(Boolean))];
 const grid=chartEditorHtml(d),doc=new DOMParser().parseFromString(grid,'text/html');const el=$('#chartEditMatrix');el.replaceWith(doc.querySelector('#chartEditMatrix'));
}
async function saveChartDraft(){const entries=$$('.matrix-rate').map(e=>({fat_value:e.dataset.fat,snf_value:e.dataset.snf,rate:e.value}));
 if(!entries.length||entries.some(e=>e.rate===''||!Number.isFinite(Number(e.rate))||Number(e.rate)<0))throw Error(t('प्रत्येक सेलमध्ये योग्य दर भरा','Fill every matrix cell with a valid rate'));
 const body={name:value('newChartName'),milk_type:value('newChartType'),chart_type:'FAT_SNF_MATRIX',effective_from:value('newChartDate'),match_policy:value('newChartPolicy'),entries};
 const chart=await post('/api/rate-charts',body);if($('#dialog').open)closeModal();state.scan=null;state.rateMilk=chart.milk_type;
 await go('rates');toast(t('दरपत्रक मसुदा तयार — आता सक्रिय करा','Draft saved — activate it when reviewed'))}
async function activateChart(id){if(!confirm(t('हे दरपत्रक सक्रिय करायचे?','Activate this chart version?')))return;
 await post(`/api/rate-charts/${id}/activate`,{});toast(t('दरपत्रक सक्रिय झाले','Chart activated'));await go('rates')}
async function previewRate(){const result=await post('/api/rate-charts/preview',{milk_type:state.rateMilk,collection_date:value('rateDate'),quantity:value('rateLitres'),fat:value('rateFat')||null,snf:value('rateSnf')||null});
 $('#rateResult').innerHTML=`<div class="notice">${t('दर / लिटर','Rate / L')}: <b>${money(result.rate_per_litre)}</b> · ${t('रक्कम','Total')}: <b>${money(result.amount)}</b></div>`}
async function customersPage(){await refreshCustomers();const items=state.customers;
 $('#view').innerHTML=`<div class="sectionline"><p class="small-muted">${t('एका टॅपमध्ये दूध, पशुखाद्य, अॅडव्हान्स आणि हिशोब','A farmer’s full account in one place')}</p>${btn('＋ '+t('शेतकरी जोडा','Add farmer'),'newCustomer','primary')}</div>
 ${panel(t('शेतकऱ्यांची यादी','Farmers'),`<div class="filterbar"><input class="search" id="customerSearch" placeholder="${t('नाव किंवा क्रमांक शोधा','Search name or code')}" aria-label="Search farmers"></div><div id="customerList">${customersTable(items)}</div>`)}`;
 $('#customerSearch').oninput=()=>{$('#customerList').innerHTML=customersTable(items.filter(x=>(x.name+' '+x.code).toLowerCase().includes(value('customerSearch').toLowerCase())))};
}
function customersTable(list){return `<div class="desktop-table">${table([t('क्रमांक','Code'),t('नाव','Name'),t('गाव','Village'),t('खाते बाकी','Balance'),''],list.map(c=>`<tr class="clickable" data-action="customerDetail" data-id="${c.id}"><td class="namecell">${clean(c.code)}</td><td>${clean(c.name)}</td><td>${clean(c.village||'—')}</td><td class="numeric ${amountColor(c.balance)}">${money(c.balance)}</td><td>›</td></tr>`).join(''))}</div>
 <div class="mobile-card-list">${list.map(c=>`<button class="record-card" data-action="customerDetail" data-id="${c.id}"><span><b>${clean(c.name)} · ${clean(c.code)}</b><small>${clean(c.village||'')}</small></span><span class="card-numbers"><strong class="${amountColor(c.balance)}">${money(c.balance)}</strong></span><span class="chevron">›</span></button>`).join('')||empty()}</div>`}
function customerDialog(){showModal(t('नवीन शेतकरी','Add farmer'),`<div class="formgrid">${input('cCode',t('शेतकरी क्रमांक','Farmer code'),'','','required')}${input('cName',t('पूर्ण नाव','Full name'),'','','required')}${input('cPhone',t('मोबाईल','Mobile'),'','tel')}${input('cVillage',t('गाव','Village'))}</div><details class="bank-details-toggle"><summary>${t('बँक तपशील (ऐच्छिक)','Bank details (optional)')}</summary>${bankAccountFields('newBank')}</details>`,btn(t('जतन करा','Save farmer'),'saveCustomer','primary'))}
function bankAccountFields(prefix,existing={}){return `<div class="formgrid" style="margin-top:13px">
 ${input(prefix+'Name',t('बँक खात्यावरील नाव','Account holder name'),existing.beneficiary_name||'','text','autocomplete="off"')}
 ${input(prefix+'Ifsc','IFSC',existing.ifsc||'','text','maxlength="11" autocapitalize="characters" autocomplete="off"')}
 ${input(prefix+'Number',t('खाते क्रमांक','Account number'),'','text','autocapitalize="characters" autocomplete="off"')}
 ${input(prefix+'Confirm',t('खाते क्रमांक पुन्हा लिहा','Confirm account number'),'','text','autocapitalize="characters" autocomplete="off"')}
 ${input(prefix+'Bank',t('बँकेचे नाव (ऐच्छिक)','Bank name (optional)'),existing.bank_name||'')}
 ${select(prefix+'Type',t('खात्याचा प्रकार','Account type'),[['',t('निवडा','Select')],['SAVINGS','Savings'],['CURRENT','Current']],existing.account_type||'')}
 ${prefix!=='bankDairy'?select(prefix+'PaymentMode',t('पेमेंट प्रकार','Payment mode'),[['NEFT','NEFT'],['FT','FT (bank transfer)'],['RTGS','RTGS']],existing.payment_mode||'NEFT'):''}
 </div><p class="small-muted">${t('खाते क्रमांक सुरक्षित साठवला जातो; फक्त शेवटचे चार अंक दाखवले जातात.','Account number is encrypted and only the last four digits are shown later.')}</p>`}
function readBankFields(prefix){return {beneficiary_name:value(prefix+'Name'),ifsc:value(prefix+'Ifsc'),account_number:value(prefix+'Number'),confirm_account_number:value(prefix+'Confirm'),bank_name:value(prefix+'Bank'),account_type:value(prefix+'Type'),payment_mode:prefix==='bankDairy'?'NEFT':value(prefix+'PaymentMode')}}
async function saveCustomer(){const bank=value('newBankNumber')?readBankFields('newBank'):null;await post('/api/customers',{code:value('cCode'),name:value('cName'),phone:value('cPhone'),village:value('cVillage'),bank_details:bank});closeModal();await refreshCustomers();toast(t('शेतकरी जोडला','Farmer added'));await go('customers')}
async function customerDetail(id){const [d,a,adv,bank]=await Promise.all([api(`/api/customers/${id}`),api(`/api/v2/anamat/${id}`),api(`/api/v2/advances?customer_id=${id}`),['OWNER','MANAGER'].includes(state.session.role)?api(`/api/v2/bank/customers/${id}`):Promise.resolve(null)]);
 showModal(`${clean(d.customer.name)} · ${clean(d.customer.code)}`,`<div class="stats" style="grid-template-columns:repeat(2,1fr)">
 ${stat(t('बाकी','Balance'),money(d.balance),'','₹')}${stat(t('अनामत','Anamat'),money(a.balance),'','⌁')}</div>
 <div class="moneyline"><span>${t('बँक खाते','Bank account')}: ${bank?.configured?clean(bank.masked_account):t('जोडलेले नाही','Not added')}</span>${bank?`<button class="iconbutton icon-edit" data-action="customerBank" data-id="${id}" aria-label="${t('बँक तपशील बदला','Edit bank details')}" title="${t('बँक तपशील बदला','Edit bank details')}">✎</button>`:''}</div><h3>${t('अॅडव्हान्स','Advances')}</h3>${adv.length?adv.map(v=>`<div class="moneyline"><span>${v.date}</span><b>${money(v.outstanding)}</b></div>`).join(''):empty(t('अॅडव्हान्स नाही','No advances'))}
 <h3>${t('खात्यातील व्यवहार','Ledger history')}</h3>${table([t('तारीख','Date'),t('तपशील','Details'),t('रक्कम','Amount')],d.entries.slice(0,50).map(e=>`<tr><td>${clean(e.date)}</td><td>${clean(e.description||e.type)}</td><td class="${amountColor(e.amount)} numeric">${money(e.amount)}</td></tr>`).join(''))}
 <div class="notice">${t('अनामत आणि अॅडव्हान्सचा हिशोब स्वतंत्र ठेवलेला आहे.','Anamat and advances have independent balances.')}</div>`,btn('WhatsApp','shareAccount','outline',`data-id="${id}"`))}
async function advancesPage(){const list=await api('/api/v2/advances');
 $('#view').innerHTML=`<div class="sectionline"><span class="small-muted">${t('अॅडव्हान्सची रक्कम आणि टर्मनुसार परतफेड','Give an advance and configure term repayment')}</span>${btn('＋ '+t('अॅडव्हान्स द्या','Give advance'),'newAdvance','primary')}</div>
 <div class="stats">${stat(t('बाकी अॅडव्हान्स','Outstanding'),money(list.reduce((s,x)=>s+Number(x.outstanding),0)),'','↗')}${stat(t('एकूण खाती','Advances'),num(list.length),'','♙')}</div>
 ${panel(t('अॅडव्हान्स यादी','Advance records'),table([t('शेतकरी','Farmer'),t('दिलेली रक्कम','Principal'),t('बाकी','Outstanding'),t('प्रति टर्म कपात','Installment')],list.map(x=>`<tr><td>${clean(state.customers.find(c=>c.id===x.customer_id)?.name||x.customer_id)}</td><td>${money(x.principal)}</td><td class="numeric">${money(x.outstanding)}</td><td>${money(x.installment)}</td></tr>`).join('')))}
 ${panel(t('अनामत वितरण','Anamat payout'),`<p class="small-muted">${t('दिवाळी किंवा इतर वेळी जमा अनामत द्या. उपलब्ध रकमेपेक्षा जास्त वितरण होणार नाही.','Record Diwali bonus payouts from accumulated anamat.')}</p>${btn(t('अनामत द्या','Pay anamat'),'anamatPayout','outline')}`)}`;
}
function advanceDialog(){showModal(t('अॅडव्हान्स द्या','Give an advance'),`<div class="formgrid">${select('aCustomer',t('शेतकरी','Farmer'),customerOptions())}${input('aAmount',t('अॅडव्हान्स रक्कम','Advance amount'),'','number','step="0.01" min="0.01"')}${input('aInstallment',t('दर १० दिवसांनी कपात','Repayment per term'),'','number','step="0.01" min="0"')}${input('aDate',t('तारीख','Date'),today(),'date')}${input('aNote',t('तपशील (ऐच्छिक)','Notes'))}</div><div class="notice" style="margin-top:14px">${t('व्याज दर सर्व डेअरीसाठी सेटिंग्जमध्ये एकदाच ठरवा.','Set interest rules once in Dairy Settings.')}</div>`,btn(t('अॅडव्हान्स जतन','Save advance'),'saveAdvance','primary'))}
async function saveAdvance(){await post('/api/v2/advances',{customer_id:Number(value('aCustomer')),principal:value('aAmount'),installment:value('aInstallment'),advance_date:value('aDate'),note:value('aNote')});closeModal();toast(t('अॅडव्हान्स जतन झाला','Advance recorded'));await go('advances')}
function anamatDialog(){showModal(t('अनामत वितरण','Pay anamat'),`<div class="formgrid">${select('anCustomer',t('शेतकरी','Farmer'),customerOptions())}${input('anAmount',t('रक्कम','Amount'),'','number','min="0.01" step="0.01"')}${input('anDate',t('तारीख','Date'),today(),'date')}${input('anNote',t('तपशील','Notes'))}</div>`,btn(t('अनामत वितरण जतन','Record payout'),'saveAnamat','primary'))}
async function saveAnamat(){await post('/api/v2/anamat/payout',{customer_id:Number(value('anCustomer')),amount:value('anAmount'),payout_date:value('anDate'),note:value('anNote')});closeModal();toast(t('अनामत दिली','Payout recorded'));await go('advances')}
async function billingPage(){const [preview,history]=await Promise.all([api(`/api/v2/billing/preview?start=${state.start}&end=${state.end}`),api('/api/v2/billing/history')]);state.billing=preview;
 const finalized=history.find(p=>p.start===state.start&&p.end===state.end&&p.status==='FINALIZED');state.bankPeriod=finalized?.id||null;
 const totals=preview.totals;const tableRows=preview.bills.filter(b=>b.has_activity).map(b=>`<tr class="clickable" data-action="billDetail" data-id="${b.customer_id}"><td class="namecell">${clean(b.code)} · ${clean(b.name)}</td><td>${money(b.milk_amount)}</td><td>${money(Number(b.feed_amount)+Number(b.advance_principal)+Number(b.advance_interest)+Number(b.anamat_amount)+Number(b.opening_carry_forward||0))}</td><td class="numeric ${amountColor(b.payable)}">${money(b.payable)}</td><td>›</td></tr>`).join('');
 $('#view').innerHTML=`<div class="sectionline"><p class="small-muted">${t('दूध + पशुखाद्य + अॅडव्हान्स + व्याज + अनामत = अंतिम हिशोब','Milk − feed − advance − interest − anamat = settlement')}</p>${finalized?`${pill(t('अंतिम हिशोब तयार','Finalized'))}${btn('▤ '+t('बँक Excel तयार करा','Bank Excel'),'bankPreview','primary')}`:btn('✓ '+t('बिलं अंतिम करा','Finalize bills'),'finalizeBills','primary')}</div>
 ${termControls()}<div class="stats">${stat(t('दूध रक्कम','Milk earnings'),money(totals.milk_amount),'','◉')}${stat(t('पशुखाद्य कपात','Feed deductions'),money(totals.feed_amount),'','▣')}${stat(t('अॅडव्हान्स + व्याज','Advance + interest'),money(Number(totals.advance_principal)+Number(totals.advance_interest)),'','↗')}${stat(t('एकूण देय','Net payable'),money(totals.payable),'','₹')}</div>
 ${panel(t('शेतकऱ्यांचा १० दिवसांचा हिशोब','10-day farmer bills'),`<div class="desktop-table">${table([t('शेतकरी','Farmer'),t('दूध रक्कम','Milk amount'),t('कपात','Deductions'),t('देय','Payable'),''],tableRows)}</div>
 <div class="mobile-card-list">${preview.bills.filter(b=>b.has_activity).map(b=>`<button class="record-card" data-action="billDetail" data-id="${b.customer_id}"><span><b>${clean(b.name)} · ${clean(b.code)}</b><small>${t('दूध','Milk')}: ${money(b.milk_amount)} · ${t('कपात','Deductions')}: ${money(Number(b.milk_amount)-Number(b.payable))}</small></span><span class="card-numbers"><strong class="${amountColor(b.payable)}">${money(b.payable)}</strong></span><span class="chevron">›</span></button>`).join('')||empty()}</div>`) }
 ${panel(t('अंतिम हिशोब इतिहास','Finalized periods'),history.length?history.map(p=>`<div class="alert"><span>${clean(p.start)} — ${clean(p.end)} · ${num(p.count)} ${t('बिलं','bills')}</span>${pill(p.status)}</div>`).join(''):empty(t('अजून हिशोब अंतिम केलेला नाही','No finalized settlement yet')))}`;
}
function billDetail(id){const bill=state.billing?.bills.find(b=>b.customer_id===Number(id));if(!bill)return;state.billToShare=bill;
 const deductions=[['पशुखाद्य / Feed',bill.feed_amount],['अॅडव्हान्स हप्ता / Advance',bill.advance_principal],['व्याज / Interest',bill.advance_interest],['अनामत / Anamat',bill.anamat_amount],['मागील बाकी / Previous carry',bill.opening_carry_forward||0]];
 const itemRows=bill.milk_lines.map(l=>`<tr><td>${clean(l.date)}${l.session?' · '+clean(l.session):''}</td><td>${num(l.litres)} L</td><td>${num(l.fat)} / ${num(l.snf)}</td><td>${money(l.rate)}</td><td>${money(l.amount)}</td></tr>`).join('');
 showModal(t('१० दिवसांचा हिशोब','10-day bill'),`<div class="receipt"><div class="receipt-head"><h2>${clean(state.session.dairy.name)}</h2><p style="margin:7px 0">${t('दूध बिल','Milk settlement')} · ${clean(bill.start)} — ${clean(bill.end)}</p><strong>${clean(bill.name)} · ${clean(bill.code)}</strong></div>
 ${table([t('तारीख','Date'),t('लिटर','Litres'),'FAT/SNF',t('दर','Rate'),t('रक्कम','Amount')],itemRows)}
 <div class="moneyline"><span>${t('एकूण दूध','Total litres')}</span><b>${num(bill.litres)} L</b></div>
 <div class="moneyline"><span>${t('दूध जमा','Milk earnings')}</span><b>${money(bill.milk_amount)}</b></div>
 ${deductions.map(([k,v])=>`<div class="moneyline"><span>${k}</span><b>− ${money(v)}</b></div>`).join('')}
 <div class="moneyline total"><span>${t('अंतिम देय','Net payable')}</span><b>${money(bill.payable)}</b></div>
 ${Number(bill.carry_forward)>0?`<div class="notice warn">${t('पुढील टर्मला बाकी','Carry forward')}: ${money(bill.carry_forward)}</div>`:''}
 <div class="small-muted" style="margin-top:12px">${t('जमा अनामत (या हिशोबापूर्वी)','Accumulated anamat before this term')}: ${money(bill.anamat_balance)}</div></div>`,`${btn('⎙ '+t('प्रिंट / PDF','Print / PDF'),'printBill','outline')}${btn('WhatsApp','shareBill','primary')}`)}
async function finalizeBills(){if(!state.billing)throw Error('No preview');if(!confirm(t('सर्व बिलं अंतिम करायची? नंतर थेट बदलता येणार नाहीत.','Finalize all bills? Final bills cannot be edited directly.')))return;
 const result=await post('/api/v2/billing/finalize',{start:state.start,end:state.end});toast(result.already_finalized?t('आधीच अंतिम झाले','Already finalized'):t('बिलं अंतिम झाली','Bills finalized'));await go('billing')}
function shareBill(){const b=state.billToShare;if(!b)return;
 const phone=b.phone?.replace(/\D/g,'');if(!phone)throw Error(t('शेतकऱ्याचा मोबाईल क्रमांक नाही','Farmer phone number missing'));
 const text=`${state.session.dairy.name}\n${b.name} (${b.code})\n${b.start} to ${b.end}\nMilk: ₹${b.milk_amount}\nFeed: -₹${b.feed_amount}\nAdvance: -₹${b.advance_principal}\nInterest: -₹${b.advance_interest}\nAnamat: -₹${b.anamat_amount}\nPrevious balance: -₹${b.opening_carry_forward||0}\nNet payable: ₹${b.payable}`;
 window.open(`https://wa.me/${phone.length===10?'91'+phone:phone}?text=${encodeURIComponent(text)}`,'_blank','noopener')}
async function shareAccount(id){const result=await post(`/api/customers/${id}/whatsapp`,{});if(result.url)window.open(result.url,'_blank','noopener');else toast(t('मेसेज पाठवण्याची विनंती झाली','Messaging requested'))}
async function reportsPage(){const d=await api(`/api/v2/reports?start=${state.start}&end=${state.end}`);
 $('#view').innerHTML=`<p class="small-muted">${t('दूध विक्री, पशुखाद्य नफा आणि खर्च यांचे साधे आकडे','Track procurement, milk sales, feed margin and expenses.')}</p>${termControls()}
 <div class="stats">${stat(t('दूध खरेदी','Milk purchase'),money(d.milk_cost),'','◉')}${stat(t('दूध विक्री','Milk sales'),money(d.milk_revenue),'','↗')}${stat(t('पशुखाद्य नफा','Feed margin'),money(d.feed_margin),'','▣')}${stat(t('खर्च','Expenses'),money(d.expenses),'','₹')}</div>
 ${panel(t('व्यवसायाचा अंदाजे नफा','Estimated operating surplus'),`<div class="statvalue" style="color:var(--forest2)">${d.complete?money(d.estimated_surplus):'—'}</div>
 <p class="small-muted">${clean(d.note)}</p><div class="notice warn">${t('अनामत आणि अॅडव्हान्सची रक्कम नफा म्हणून मोजलेली नाही.','Anamat and advance repayments are never counted as business profit.')}</div>`)}
 ${panel(t('दूध विक्री आणि खर्च भरा','Record sales and expenses'),`<div class="action-grid">${actionTile('↗',t('दूध विक्री','Milk dispatch'),t('खरेदीदाराला विक्री','Milk sold to buyer'),'reports')}${actionTile('₹',t('इतर खर्च','Operating expense'),t('वीज, वाहतूक, कामगार','Transport, power, labour'),'reports')}</div>
 <div class="actions" style="margin-top:12px">${btn('＋ '+t('दूध विक्री भरा','Add dispatch'),'newDispatch','primary')}${btn('＋ '+t('खर्च भरा','Add expense'),'newExpense')}</div>`)}`;
}
function dispatchDialog(){showModal(t('दूध विक्री नोंद','Record milk dispatch'),`<div class="formgrid">${input('dDate',t('तारीख','Date'),today(),'date')}${input('dLitres',t('विकलेले लिटर','Litres sold'),'','number','min="0.001" step="0.001"')}${input('dAmount',t('विक्रीची एकूण रक्कम','Total revenue'),'','number','min="0" step="0.01"')}${input('dBuyer',t('खरेदीदार','Buyer'))}</div>`,btn(t('जतन करा','Save dispatch'),'saveDispatch','primary'))}
async function saveDispatch(){await post('/api/v2/dispatch',{dispatch_date:value('dDate'),litres:value('dLitres'),amount:value('dAmount'),buyer:value('dBuyer')});closeModal();toast(t('दूध विक्री जतन झाली','Dispatch saved'));await go('reports')}
function expenseDialog(){showModal(t('खर्च भरा','Record expense'),`<div class="formgrid">${input('eDate',t('तारीख','Date'),today(),'date')}${select('eCategory',t('खर्च प्रकार','Category'),[['transport',t('वाहतूक','Transport')],['electricity',t('वीज','Electricity')],['labour',t('कामगार','Labour')],['maintenance',t('दुरुस्ती','Maintenance')],['other',t('इतर','Other')]])}${input('eAmount',t('रक्कम','Amount'),'','number','min="0.01" step="0.01"')}${input('eNote',t('तपशील','Notes'))}</div>`,btn(t('जतन करा','Save expense'),'saveExpense','primary'))}
async function saveExpense(){await post('/api/v2/expenses',{expense_date:value('eDate'),category:value('eCategory'),amount:value('eAmount'),note:value('eNote')});closeModal();toast(t('खर्च जतन झाला','Expense saved'));await go('reports')}
async function settingsPage(){const policies=await api('/api/settings/financial-policy');const p=policies[0]||{};
 $('#view').innerHTML=`<div class="narrow"><p class="small-muted">${t('दर आणि नियम एकदाच सेट करा. नवीन नियम लागू तारखेनुसार चालतील.','Configure policies once for the dairy. Versions apply by date.')}</p>
 ${panel(t('डेअरी आर्थिक नियम','Dairy financial settings'),`<div class="formgrid">${input('pFrom',t('लागू तारीख','Effective from'),today(),'date')}
 ${input('pAnamat',t('अनामत ₹/लिटर','Anamat ₹ per litre'),p.anamat_rate||'1','number','min="0" step="0.01"')}
 ${input('pInterest',t('वार्षिक व्याज दर (%)','Annual interest (%)'),p.annual_interest_rate||'0','number','min="0" step="0.01"')}
 ${select('pInterestMethod',t('व्याज मोजणी','Interest method'),[['REDUCING_BALANCE',t('उर्वरित रकमेवर','Reducing balance')],['SIMPLE',t('मूळ रकमेवर','Simple principal')]],p.interest_method||'REDUCING_BALANCE')}
 ${select('pInterestPeriod',t('व्याज कालावधी','Interest period'),[['MONTHLY',t('महिन्याचे','Monthly')],['PER_BILLING_TERM',t('टर्मनुसार','Per term')]],p.interest_period||'MONTHLY')}
 ${select('pDayCount',t('वर्षातील दिवस','Day-count basis'),[['ACTUAL_365','365'],['ACTUAL_360','360']],p.day_count_convention||'ACTUAL_365')}
 </div>
 <div class="actions" style="margin-top:17px">${btn(t('नवीन नियम जतन करा','Save new policy version'),'savePolicy','primary')}</div>
 <div class="notice" style="margin-top:12px">${t('व्याज दर हा वार्षिक टक्केवारी आहे. प्रत्यक्ष दिवसांनुसार हिशोब होतो.','Interest is an annual percentage prorated by actual days.')}</div>`)}
 ${state.session.role==='OWNER'?panel(t('डेअरीचे पैसे जाणारे बँक खाते','Dairy debit bank account'),`<div class="moneyline"><span id="dairyBankStatus">${t('खाते तपासत आहे…','Loading bank account…')}</span><button class="iconbutton icon-edit" data-action="dairyBank" aria-label="${t('बँक खाते बदला','Edit dairy bank account')}" title="${t('बँक खाते बदला','Edit dairy bank account')}">✎</button></div><p class="small-muted">${t('शेतकऱ्यांना बँकेने पैसे देण्यासाठी हे खाते वापरले जाईल.','Account used as debit account in the payment Excel file.')}</p>`):''}
 ${panel(t('इतर सेटिंग्ज','Other settings'),`<div class="moneyline"><span>${t('डेअरी','Dairy')}</span><b>${clean(state.session.dairy.name)}</b></div><div class="moneyline"><span>${t('तुमचा अधिकार','Your role')}</span><b>${clean(state.session.role)}</b></div><div class="moneyline"><span>${t('तुमची भाषा','Language')}</span><b>${state.lang==='mr'?'मराठी':'English'}</b></div><div class="actions" style="margin-top:10px">${btn(t('भाषा बदला','Change language'),'language')}</div>`)}
 ${panel(t('स्कॅन इंजिन','AI extraction'),`<div id="aiHealth">${loading()}</div>`)}</div>`;
 if(state.session.role==='OWNER'){const b=await run(()=>api('/api/v2/bank/dairy'));if($('#dairyBankStatus'))$('#dairyBankStatus').textContent=b?.configured?`${b.masked_account} · ${b.bank_name||b.ifsc}`:t('अजून खाते जोडलेले नाही','Debit account not configured')}
 const result=await run(()=>api('/api/ai/health'));$('#aiHealth').innerHTML=result?`<div class="moneyline"><span>${clean(result.provider)}</span>${pill(result.connected?t('जोडलेले','Connected'):t('तपासा','Check'),result.connected?'':'warn')}</div><small>${clean(result.message||result.model||'')}</small>`:`<div class="notice warn">${t('AI सेवा तपासता आली नाही','AI provider unavailable')}</div>`;
}
async function savePolicy(){const interest=Number(value('pInterest'));const anamat=Number(value('pAnamat'));const body={effective_from:value('pFrom'),
 anamat_enabled:anamat>0,anamat_rate:anamat,anamat_cow_rate:null,anamat_buffalo_rate:null,
 interest_enabled:interest>0,annual_interest_rate:interest,interest_period:value('pInterestPeriod'),
 interest_method:value('pInterestMethod'),day_count_convention:value('pDayCount'),billing_term_days:10};
 await post('/api/settings/financial-policy',body);toast(t('नवीन नियम जतन झाले','Policy version saved'));await go('settings')}
function assistantPage(){$('#view').innerHTML=`<section class="panel narrow"><div class="panelhead"><h2>${t('मराठी सहाय्यक','Marathi assistant')}</h2>${pill('AI')}</div>
 <p class="small-muted">${t('उदा. D001 या शेतकऱ्याची बाकी किती?','Ask about a farmer’s milk, feed, advance or balance.')}</p>
 <div class="field"><label>${t('तुमचा प्रश्न','Your question')}</label><textarea id="assistantInput" rows="3" placeholder="${t('D001 ची बाकी किती आहे?','What is D001 balance?')}"></textarea></div>
 <div class="actions" style="margin-top:12px">${btn('🎙 '+t('बोला','Speak'),'voiceInput')}${btn(t('विचारा','Ask DairyAI'),'assistantAsk','primary')}</div><div id="assistantResponse" style="margin-top:16px"></div>
 <div class="notice" style="margin-top:17px">${t('AI ला फक्त विचारलेल्या खात्याची तपासलेली माहिती दिली जाते.','AI receives only the server-verified account facts needed for this question.')}</div></section>`}
async function askAssistant(){const q=value('assistantInput').trim();if(!q)throw Error(t('प्रश्न लिहा','Enter a question'));$('#assistantResponse').innerHTML=loading();const r=await post('/api/assistant',{query:q});$('#assistantResponse').innerHTML=`<div class="notice">${clean(r.answer)}</div>`}
function voiceInput(){const Speech=window.SpeechRecognition||window.webkitSpeechRecognition;if(!Speech)throw Error(t('या ब्राउजरमध्ये आवाज सुविधा उपलब्ध नाही','Voice recognition not supported in this browser'));
 const recognizer=new Speech();recognizer.lang=state.lang==='mr'?'mr-IN':'en-IN';recognizer.onresult=e=>{$('#assistantInput').value=e.results[0][0].transcript};recognizer.onerror=e=>toast(String(e.error),true);recognizer.start()}

async function customerBankDialog(el){const id=Number(el.dataset.id);state.bankCustomerId=id;
 const current=await api(`/api/v2/bank/customers/${id}`);
 const customer=state.customers.find(c=>c.id===id);
 showModal(`${t('बँक खाते','Bank account')} · ${clean(customer?.name||id)}`,
 `<div class="notice">${current.configured?`${t('सध्याचे खाते','Current account')}: ${clean(current.masked_account)} · ${clean(current.ifsc)}`:t('खाते जोडलेले नाही','No bank account saved')}</div>${bankAccountFields('bankFarmer',current)}`,
 btn(t('सुरक्षितपणे जतन करा','Save securely'),'saveFarmerBank','primary'))}
async function saveFarmerBank(){await put(`/api/v2/bank/customers/${state.bankCustomerId}`,readBankFields('bankFarmer'));closeModal();toast(t('बँक खाते जतन झाले','Bank account saved'));
 if(state.bankPeriod)await bankPreviewDialog();}
async function dairyBankDialog(){const current=await api('/api/v2/bank/dairy');showModal(t('डेअरी बँक खाते','Dairy debit bank account'),
 `<div class="notice">${current.configured?`${t('सध्याचे खाते','Current account')}: ${clean(current.masked_account)}`:t('बँक खाते जोडलेले नाही','No bank account saved')}</div>${bankAccountFields('bankDairy',current)}`,
 btn(t('सुरक्षितपणे जतन करा','Save securely'),'saveDairyBank','primary'))}
async function saveDairyBank(){await put('/api/v2/bank/dairy',readBankFields('bankDairy'));closeModal();toast(t('डेअरी खाते जतन झाले','Dairy bank account saved'));await go('settings')}
async function bankPreviewDialog(){if(!state.bankPeriod)throw Error(t('बिलं आधी अंतिम करा','Finalize bills first'));
 const result=await api(`/api/v2/bank/billing/${state.bankPeriod}/preview`);state.bankPreview=result;
 const issues=result.missing.map(m=>`<div class="moneyline bank-issue"><span><b>${clean(m.code)} · ${clean(m.name)}</b><small>${clean(m.reason)}</small></span>${m.customer_id?`<button class="iconbutton icon-edit" data-action="customerBank" data-id="${m.customer_id}" title="${t('खाते जोडा','Add bank account')}" aria-label="${t('खाते जोडा','Add bank account')}">✎</button>`:btn(t('सेटिंग्ज','Settings'),'goBankSettings')}</div>`).join('');
 showModal(t('बँक पेमेंट Excel','Bank payment Excel'),`<div class="stack"><div class="notice">${t('फक्त अंतिम हिशोबात देय रक्कम असलेले शेतकरी. फाईल डाउनलोड केल्याने पैसे पाठवले जात नाहीत.','Only positive payouts from finalized bills. Downloading does not send money.')}</div>
 <div class="stats" style="grid-template-columns:repeat(2,minmax(0,1fr))">${stat(t('तयार नोंदी','Ready'),num(result.ready),'','♙')}${stat(t('रक्कम','Total payout'),money(result.total),'','₹')}</div>
 <div class="formgrid"><div class="field"><label for="bankPaymentDate">${t('पेमेंटची तारीख','Payment date')}</label><input id="bankPaymentDate" type="date" value="${today()}" required></div><div class="field"><label>${t('ICICI पेमेंट टेम्पलेट','Bank upload format')}</label><div class="small-muted">ICICI · PAB_VENDOR · 19 columns</div></div></div>
 ${result.exported_before?`<div class="notice warn">⚠ ${t('या टर्मची फाईल आधी डाउनलोड झाली आहे. पुन्हा पाठवताना दुबार पेमेंट टाळा.','This term was exported previously. Avoid duplicate bank submission.')}</div>`:''}
 ${result.missing.length?`<h3>${t('बँक माहिती पूर्ण करा','Complete bank details')}</h3>${issues}`:`<div class="alert ok">✓ ${t('सर्व बँक खाती तयार आहेत','All beneficiary accounts ready')}</div>`}
 <small>${t('तुमच्या ICICI नमुन्याप्रमाणे १९ स्तंभ. बँकेत अपलोड करण्यापूर्वी खाते, रक्कम आणि पेमेंट प्रकार तपासा.','19 columns matching your ICICI sample. Verify beneficiary details, amounts and modes with the bank before upload.')}</small></div>`,result.missing.length?'':btn('⬇ '+t('Excel डाउनलोड करा','Download Excel'),'downloadBankExcel','primary'))}
function goBankSettings(){closeModal();go('settings')}
async function downloadBankExcel(){const period=state.bankPeriod;const res=await fetch(`/api/v2/bank/billing/${period}/export?payment_date=${encodeURIComponent(value('bankPaymentDate')||today())}`,{method:'POST',headers:{Authorization:`Bearer ${state.token}`}});
 if(!res.ok){let detail;try{detail=(await res.json()).detail}catch{detail=`HTTP ${res.status}`};throw Error(typeof detail==='string'?detail:JSON.stringify(detail))}
 const blob=await res.blob();const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=`ICICI_PAB_Payments_${state.start}_${state.end}.xlsx`;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),5000);
 closeModal();toast(t('Excel डाउनलोड झाले; बँकेत पाठवण्यापूर्वी तपासा','Excel downloaded; verify before bank upload'))}

const handlers={
 scanGo:()=>{state.scanType='auto';state.scan=null;return go('scan')},milkGo:()=>go('milk'),
 manualMilk:manualMilkDialog,saveMilk,saveMilkCorrection,milkDetail:el=>{state.editReturnPage='milk';milkDetails(el.dataset.id)},homeFarmer,editHomeMilk,toggleAllFarmers:()=>{state.showAllFarmers=!state.showAllFarmers;return go('home')},
 scanType:el=>{state.scanType=el.dataset.value;$$('#scanTypes .chip').forEach(x=>x.classList.toggle('active',x===el))},
 scanUpload,takeScanPhoto:()=>$('#scanCamera')?.click(),selectScanPhoto:()=>$('#scanFile')?.click(),openBatch:el=>openBatch(el.dataset.id),reviewRow:el=>scanRowDialog(el.dataset.id),saveScanRow,addScanRow:addScanRowDialog,saveNewRow,approveScan,
 feedTab:el=>{state.feedTab=el.dataset.value;return go('feed')},
 feedScan:()=>{state.scan=null;state.scanType='feed';return go('scan')},
 manualSale:manualSaleDialog,saveSale,newProduct:newProductDialog,saveProduct,bulkStock:bulkStockDialog,saveBulkStock,
 addStockRow:()=>{$('#stockRows').insertAdjacentHTML('beforeend',stockRow())},
 removeStockRow:el=>{if($$('.bulk-row').length>1)el.closest('.bulk-row').remove();else toast(t('किमान एक ओळ हवी','Keep one stock row'))},
 chartScan:()=>{state.scan=null;state.scanType='rate_chart';return go('scan')},
 rateMilk:el=>{state.rateMilk=el.dataset.value;return go('rates')},editChart:editChartDialog,rebuildGrid,saveChartDraft,
 activateChart:el=>activateChart(el.dataset.id),previewRate,
 newCustomer:customerDialog,saveCustomer,customerDetail:el=>customerDetail(el.dataset.id),customerBank:customerBankDialog,saveFarmerBank,dairyBank:dairyBankDialog,saveDairyBank,goBankSettings,bankPreview:bankPreviewDialog,downloadBankExcel,shareAccount:el=>shareAccount(el.dataset.id),
 newAdvance:advanceDialog,saveAdvance,anamatPayout:anamatDialog,saveAnamat,
 billDetail:el=>billDetail(el.dataset.id),finalizeBills,printBill:()=>window.print(),shareBill,
 newDispatch:dispatchDialog,saveDispatch,newExpense:expenseDialog,saveExpense,
 savePolicy,assistantAsk:askAssistant,voiceInput,
 currentTerm:()=>{Object.assign(state,termFor(today()));return go(state.page)},
 changeTerm:()=>{const start=value('periodStart'),end=value('periodEnd');if(!start||!end||end<start)throw Error(t('योग्य कालावधी निवडा','Choose a valid date range'));state.start=start;state.end=end;return go(state.page)},
 close:closeModal,authMode:()=>{registering=!registering;authPage()},
 language:()=>{state.lang=state.lang==='mr'?'en':'mr';localStorage.setItem('dairyai_language',state.lang);document.documentElement.lang=state.lang;return state.session?go(state.page):authPage()},
 logout:()=>{state.token='';sessionStorage.removeItem('dairyai_token');registering=false;authPage()}
};
document.addEventListener('click',event=>{const page=event.target.closest('[data-page]');if(page){go(page.dataset.page);return}
 const target=event.target.closest('[data-action]');if(!target)return;event.preventDefault();const action=handlers[target.dataset.action];if(!action)return;
 if(target.disabled)return;target.disabled=true;Promise.resolve().then(()=>action(target)).catch(alertError).finally(()=>{if(target.isConnected)target.disabled=false})});
$('#dialog').addEventListener('click',event=>{if(event.target===$('#dialog'))closeModal()});
$('#dialog').addEventListener('input',event=>{if(event.target.id?.startsWith('cm')){clearTimeout(cmPreviewTimer);cmPreviewTimer=setTimeout(previewCorrectedMilk,330)}});
if('serviceWorker' in navigator && (location.protocol==='https:'||location.hostname==='localhost')){
 navigator.serviceWorker.register('/service-worker.js').catch(()=>{});
}
init();

const csrf = document.querySelector('meta[name="csrf"]').content;
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const txt = (v) => esc(v).replace(/\n/g,' ');
const dateFormatter = new Intl.DateTimeFormat('ru-RU',{day:'2-digit',month:'short',year:'numeric'});
const dateStr = (v) => { if (!v) return '—'; const d = new Date(v); return Number.isNaN(d.getTime()) ? String(v).slice(0,16) : dateFormatter.format(d); };
const scoreClass = (s) => s >= 65 ? '' : (s >= 35 ? 'mid' : 'low');
const stageNames = {new:'Новый',review:'На проверке',contacted:'Обработано',won:'Результат',not_relevant:'Не подходит'};
const state = {view:'overview',page:1,pages:1,filter:{q:'',category:'',stage:'',min_score:'35',only_potential:'0',referral_only:'0',signals_only:'0'}};
function toast(message,bad=false){ const t=$('toast'); t.textContent=message;t.classList.toggle('bad',bad);t.classList.remove('hidden');clearTimeout(window.toastTimer);window.toastTimer=setTimeout(()=>t.classList.add('hidden'),4200); }
async function api(path,opts={}){ let options={credentials:'same-origin',...opts}; if(options.body && typeof options.body !=='string'){options.body=JSON.stringify(options.body);} if(options.method && !['GET','HEAD'].includes(options.method)){options.headers={...options.headers,'Content-Type':'application/json','X-CSRF-Token':csrf};} const response=await fetch(path,options);let json;try{json=await response.json();}catch(e){throw new Error('Не удалось прочитать ответ сервера');} if(!response.ok) {if(response.status===401)location.href='/login';throw new Error(json.error || 'Ошибка сервера');}return json;}
function query(){const p=new URLSearchParams({...state.filter,page:String(state.page)});return p.toString();}
function empty(heading,sub){return `<div class="empty"><div class="empty-graphic">◌</div><strong>${esc(heading)}</strong><span>${esc(sub)}</span></div>`;}
function link(url){try{const u=new URL(url);return ['https:','http:'].includes(u.protocol)?esc(u.toString()):'#';}catch(e){return '#';}}
async function show(view){
  if(!['overview','leads','opportunities','cards','card_signals','referrals','sources','settings'].includes(view))return;
  state.view=view;state.page=1;
  const cards=view==='cards'||view==='referrals'||view==='card_signals';
  const focused=view==='opportunities'||cards;
  state.filter.only_potential=(focused&&view!=='card_signals')?'1':'0';
  state.filter.signals_only=view==='card_signals'?'1':'0';
  state.filter.min_score=view==='card_signals'?'0':(state.defaultMinScore||'35');
  $('filter-score').value=state.filter.min_score;
  state.filter.category=cards?'Карты':'';
  state.filter.referral_only=view==='referrals'?'1':'0';
  $('filter-category').value=state.filter.category;
  document.querySelectorAll('.view').forEach(el=>el.classList.add('hidden'));
  $('view-'+(focused||view==='leads'?'leads':view)).classList.remove('hidden');
  document.querySelectorAll('.nav-link').forEach(el=>el.classList.toggle('active',el.dataset.view===view));
  if(view==='overview')await loadOverview();
  if(view==='leads'||focused){
    $('leads-heading').textContent=view==='card_signals'?'Карты — на проверку':view==='referrals'?'Ищут приглашение':(view==='cards'?'Клиенты на карты':(view==='opportunities'?'Возможные клиенты':'Все публикации'));
    $('leads-subtitle').textContent=view==='referrals'
      ?'Публичные вопросы о реферальных ссылках на дебетовые карты. Не означает согласия на рекламу.'
      :view==='card_signals'?'Вопросы по картам за последние 7 дней с датой публикации. Нет подтверждённой даты — нет статуса свежего обращения.'
      :view==='cards'?'Вопросы об оформлении дебетовых карт за последние 7 дней по дате источника. Проверяй дату исходного сообщения.'
      :view==='opportunities'?'Только вопросы за последние 7 дней по дате источника; не подтверждённые заявки.'
      :'Все записи, включая статьи и новости.';
    await loadLeads();
  }
  if(view==='sources')await Promise.all([loadSources(),loadWebStatus(),loadDeepSeekStatus()]);
  if(view==='settings')await loadSettings();
  window.scrollTo({top:0,behavior:'smooth'});
}
async function loadStats(){const s=await api('/api/stats');$('stat-total').textContent=s.total.toLocaleString('ru-RU');$('stat-priority').textContent=s.opportunities.toLocaleString('ru-RU');$('stat-working').textContent=s.working.toLocaleString('ru-RU');$('stat-wins').textContent=s.wins.toLocaleString('ru-RU');$('nav-count').textContent=s.opportunities;$('nav-count-cards').textContent=s.card_requests;$('nav-count-card-signals').textContent=s.card_signals;$('nav-count-referrals').textContent=s.referral_requests;$('last-run').textContent=s.refresh.running?'Проверка выполняется…':(s.refresh.last_run?'Проверено '+dateStr(s.refresh.last_run):'Ожидание первой проверки');const max=Math.max(1,...s.categories.map(v=>v.count));$('category-bars').innerHTML=s.categories.length?s.categories.map(c=>`<div class="bar-row"><div class="bar-top"><span>${esc(c.category)}</span><span>${c.count}</span></div><progress class="bar-track" max="${max}" value="${c.count}"></progress></div>`).join(''):empty('Пока нет данных','После первой проверки появится статистика.');return s;}
async function loadOverview(){const [,p]=await Promise.all([loadStats(),api('/api/leads?only_potential=1&min_score=0&page=1&limit=5')]);const target=$('preview-list');target.innerHTML=p.items.length?p.items.slice(0,5).map(l=>`<div class="preview-item"><div><h3><a href="${link(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.title)}</a></h3><div class="preview-info"><span class="tag">${esc(l.category)}</span>${l.referral_signal==='asked'&&l.intent_label==='potential'?'<span class="tag green">Ищет приглашение</span>':''}<span class="tiny">${esc(l.source_name)} · Источник: ${l.published_on?esc(l.published_on):'дата не подтверждена'} · Сохранено ${dateStr(l.created_at)}</span></div></div><span class="score-pill ${scoreClass(l.score)}">${l.score}/100</span></div>`).join(''):empty('Пока нет явных запросов','Общие новостные RSS часто не содержат вопросов предпринимателей. Добавьте публичные ленты обсуждений.');}
function rowTemplate(l){const choices=Object.entries(stageNames).map(([value,label])=>`<option value="${value}" ${l.stage===value?'selected':''}>${label}</option>`).join('');return `<article class="lead-item" data-lead-id="${l.id}"><div class="lead-heading"><div><h3><a href="${link(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.title)} ↗</a></h3><div class="lead-meta"><span class="tag ${l.category==='РКО'?'green':(l.category==='Карты'?'blue':'')}">${esc(l.category)}</span><span class="tiny">${esc(l.source_name)} · Источник: ${l.published_on?esc(l.published_on):'дата не подтверждена'} · Сохранено ${dateStr(l.created_at)}</span><span class="tag grey">${esc(stageNames[l.stage]||l.stage)}</span><span class="tag ${l.intent_label==='potential'?'green':'grey'}">${l.intent_label==='potential'?'Возможный клиент':(l.intent_label==='information'?'Информация':'Без явного запроса')}</span></div></div><span class="score-large ${scoreClass(l.score)}">${l.score}<span>из 100</span></span></div>${l.description?`<p class="lead-description">${esc(l.description.slice(0,520))}${l.description.length>520?'…':''}</p>`:''}<p class="lead-reason">${esc(l.reason)}</p>${l.ai_verdict? `<details class="ai-details"><summary>✦ Оценка DeepSeek · ${l.ai_verdict==='potential'?'Возможный клиент':l.ai_verdict==='possible'?'Нужно проверить':'Информация'} · уверенность ${esc(l.ai_confidence)}/100</summary><p>${esc(l.ai_reason||'')}</p>${l.ai_reply?`<div class="ai-draft"><strong>Черновик ответа (проверить перед отправкой)</strong><p>${esc(l.ai_reply)}</p></div>`:''}</details>`:''}<div class="lead-bottom"><span class="tiny">Публичная публикация · подтвердите намерение вручную</span><div class="lead-controls"><select class="stage-select" aria-label="Статус записи">${choices}</select><button class="link-btn notes-toggle">✎ Заметка</button><button class="link-btn delete-lead">Удалить</button></div></div><div class="lead-notes hidden"><input type="text" class="note-input" maxlength="4000" placeholder="Комментарий (только для вас)" value="${esc(l.notes)}"><button class="btn btn-outline btn-sm save-note">Сохранить</button></div></article>`;}
async function loadLeads(){const data=await api('/api/leads?'+query());state.pages=data.pages;$('page-label').textContent=`${state.page} / ${data.pages}`;$('leads-summary').textContent=`Найдено: ${data.count}`;$('page-prev').disabled=state.page<=1;$('page-next').disabled=state.page>=data.pages;$('leads-list').innerHTML=data.items.length?data.items.map(rowTemplate).join(''):empty('Подходящих публикаций нет','Уменьшите порог оценки или подключите дополнительные источники.');}
async function loadSources(){const [data,diag]=await Promise.all([api('/api/sources'),api('/api/diagnostics')]);$('source-list').innerHTML=data.items.length?data.items.map(s=>`<div class="source-item" data-source-id="${s.id}"><div><strong>${s.enabled?'🟢':'⚪'} ${esc(s.name)}</strong><small><a href="${link(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.url)}</a></small><span class="tiny">${s.checked_at?'Последняя проверка: '+dateStr(s.checked_at):'Ещё не проверялся'}</span>${s.last_error?`<small class="source-error">Ошибка: ${esc(s.last_error)}</small>`:''}</div><div class="source-buttons"><button class="icon-button toggle-source" data-enabled="${s.enabled?'0':'1'}" title="${s.enabled?'Выключить':'Включить'}">${s.enabled?'◫':'▶'}</button><button class="icon-button delete-source" title="Удалить">×</button></div></div>`).join(''):empty('Нет RSS-источников','Добавьте первую ленту справа.');$('source-health').innerHTML=diag.items.length?diag.items.map(s=>`<div class="health-row"><strong>${esc(s.name)}</strong><span>${s.possible_requests||0} вопросов / ${s.publications||0} публикаций</span></div>`).join(''):empty('Нет данных','Подключите источник.');}
async function loadSettings(){const [t,s]=await Promise.all([api('/api/notifications'),api('/api/settings')]);$('telegram-status').textContent=t.configured?'✓ Telegram подключён':'Telegram пока не подключён';$('settings-form').elements.poll_minutes.value=String(s.poll_minutes);$('settings-form').elements.min_score.value=String(s.min_score);state.defaultMinScore=String(s.min_score);state.filter.min_score=String(s.min_score);$('filter-score').value=String(s.min_score);}

async function loadWebStatus(){
  const s=await api('/api/web-search/status');
  $('web-state').textContent=s.running?'⏳ Выполняется поиск':(s.configured?'✓ Поиск подключён':'Нужен ключ Tavily');
  $('web-results').textContent=String(s.results);
  $('web-budget').textContent=s.used_today+' / '+s.daily_limit;
  $('web-last').textContent=s.last_run?dateStr(s.last_run):'Ещё не запускался';
  $('web-search-btn').disabled=!s.configured||s.running||s.used_today>=s.daily_limit;
  $('web-help').classList.toggle('hidden',s.configured);
  $('web-search-diagnostics').textContent=s.last_run?'Последний запуск: получено '+s.received+' страниц · добавлено '+s.added+' · исключено '+s.filtered+' (старые '+s.stale+', без даты '+s.undated+') · дубли '+s.duplicates:'После первой проверки здесь появится статистика результатов API.';
  if(s.errors?.length) $('web-state').title=s.errors.join('; ');
  return s;
}
$('web-search-btn').addEventListener('click',async()=>{
  const b=$('web-search-btn');b.disabled=true;
  try{
    const data=await api('/api/web-search/run',{method:'POST',body:{}});
    toast(data.message);
    let polls=0;
    const timer=setInterval(async()=>{
      try{
        const s=await loadWebStatus();
        if(!s.running || ++polls>=50){
          clearInterval(timer);
          b.disabled=!s.configured||s.used_today>=s.daily_limit;
          if(s.errors?.length) toast('Поиск завершён с предупреждениями: '+s.errors[0],true);
          else toast('Поиск завершён. Новые публикации: '+s.added);
          if(state.view==='sources') await loadSources();
        }
      }catch(e){clearInterval(timer);b.disabled=false;toast(e.message,true);}
    },3000);
  }catch(e){b.disabled=false;toast(e.message,true);}
});


async function loadDeepSeekStatus(){
  const s=await api('/api/deepseek/status');
  $('deepseek-state').textContent=s.running?'⏳ Поиск выполняется':(s.configured?(s.ai_enabled?'✓ Поиск и анализ ИИ включены':'✓ Поиск включён, ИИ выключен'):'Не подключён');
  $('deepseek-results').textContent=String(s.results);
  $('deepseek-search-budget').textContent=s.search_used+' / '+s.search_limit;
  $('deepseek-ai-budget').textContent=s.analysis_used+' / '+s.analysis_limit;
  $('deepseek-help').classList.toggle('hidden',s.configured);
  $('deepseek-diagnostics').textContent=s.last_run?'Последний запуск: получено '+s.received+' страниц · добавлено '+s.added+' · исключено '+s.filtered+' (старые '+s.stale+', без даты '+s.undated+') · дубли '+s.duplicates:'После первой проверки здесь появится статистика результатов API.';
  $('deepseek-btn').disabled=!s.configured||s.running||s.search_used>=s.search_limit;
  if(s.errors?.length) $('deepseek-state').title=s.errors.join('; ');
  return s;
}
$('deepseek-btn').addEventListener('click',async()=>{
  const button=$('deepseek-btn');button.disabled=true;
  try{
    const before=await loadDeepSeekStatus();
    const res=await api('/api/deepseek/run',{method:'POST',body:{}});
    toast(res.message);
    let ticks=0;
    const timer=setInterval(async()=>{
      try{
        const current=await loadDeepSeekStatus();
        if((current.last_run && current.last_run!==before.last_run) || ++ticks>=60){
          clearInterval(timer);
          button.disabled=!current.configured||current.search_used>=current.search_limit;
          toast(current.errors?.length?'DeepSeek: '+current.errors[0]:'DeepSeek: поиск завершён. Новые записи: '+current.added,!!current.errors?.length);
          if(state.view==='sources')await loadSources();
        }
      }catch(e){clearInterval(timer);button.disabled=false;toast(e.message,true)}
    },5000);
  }catch(e){button.disabled=false;toast(e.message,true);}
});

function openModal(){ $('modal').classList.remove('hidden'); $('manual-form').elements.title.focus(); }
function closeModal(){ $('modal').classList.add('hidden'); }
document.querySelectorAll('[data-view]').forEach(btn=>btn.addEventListener('click',()=>show(btn.dataset.view).catch(e=>toast(e.message,true))));
document.querySelectorAll('[data-go]').forEach(btn=>btn.addEventListener('click',()=>show(btn.dataset.go).catch(e=>toast(e.message,true))));
$('refresh-btn').addEventListener('click',async()=>{try{const b=$('refresh-btn');b.disabled=true;const res=await api('/api/refresh',{method:'POST',body:{}});toast(res.message);let n=0;const interval=setInterval(async()=>{try{const s=await loadStats();if(!s.refresh.running || ++n>=30){clearInterval(interval);b.disabled=false;if(s.refresh.errors?.length)toast('Некоторые источники недоступны: проверьте раздел «Источники»',true);await show(state.view);}}catch(e){clearInterval(interval);b.disabled=false;toast(e.message,true);}},2500);}catch(e){$('refresh-btn').disabled=false;toast(e.message,true);}});
$('add-lead-btn').addEventListener('click',openModal);$('quick-manual').addEventListener('click',openModal);$('modal-close').addEventListener('click',closeModal);$('modal-cancel').addEventListener('click',closeModal);$('modal').addEventListener('click',e=>{if(e.target===$('modal'))closeModal();});document.addEventListener('keydown',e=>{if(e.key==='Escape')closeModal();});
$('manual-form').addEventListener('submit',async(e)=>{e.preventDefault();const form=e.target;try{const data=Object.fromEntries(new FormData(form));await api('/api/manual',{method:'POST',body:data});form.reset();closeModal();toast('Обсуждение добавлено');await show(state.view);}catch(err){toast(err.message,true);}});
$('source-form').addEventListener('submit',async(e)=>{e.preventDefault();try{await api('/api/sources',{method:'POST',body:Object.fromEntries(new FormData(e.target))});e.target.reset();toast('RSS-источник добавлен');await loadSources();}catch(err){toast(err.message,true);}});
$('source-list').addEventListener('click',async(e)=>{const b=e.target.closest('button');if(!b)return;const id=b.closest('[data-source-id]').dataset.sourceId;try{if(b.classList.contains('toggle-source')){await api('/api/sources/'+id,{method:'PATCH',body:{enabled:b.dataset.enabled==='1'}});toast('Источник обновлён');}if(b.classList.contains('delete-source')){if(!confirm('Удалить источник? Сохранённые публикации останутся.'))return;await api('/api/sources/'+id,{method:'DELETE',body:{}});toast('Источник удалён');}await loadSources();}catch(err){toast(err.message,true);}});
$('leads-list').addEventListener('change',async(e)=>{if(!e.target.classList.contains('stage-select'))return;const id=e.target.closest('[data-lead-id]').dataset.leadId;try{await api('/api/leads/'+id,{method:'PATCH',body:{stage:e.target.value}});toast('Статус обновлён');await loadLeads();}catch(err){toast(err.message,true);}});
$('leads-list').addEventListener('click',async(e)=>{const b=e.target.closest('button');if(!b)return;const item=b.closest('[data-lead-id]');const id=item.dataset.leadId;try{if(b.classList.contains('notes-toggle'))item.querySelector('.lead-notes').classList.toggle('hidden');if(b.classList.contains('save-note')){await api('/api/leads/'+id,{method:'PATCH',body:{notes:item.querySelector('.note-input').value}});toast('Заметка сохранена');item.querySelector('.lead-notes').classList.add('hidden');}if(b.classList.contains('delete-lead')){if(!confirm('Удалить запись? Она может появиться снова при проверке RSS.'))return;await api('/api/leads/'+id,{method:'DELETE',body:{}});toast('Запись удалена');await loadLeads();}}catch(err){toast(err.message,true);}});
let debounce;for(const [id,key] of [['lead-search','q'],['filter-category','category'],['filter-stage','stage'],['filter-score','min_score']]){$(id).addEventListener(id==='lead-search'?'input':'change',e=>{clearTimeout(debounce);debounce=setTimeout(async()=>{state.filter[key]=e.target.value;state.page=1;try{await loadLeads();}catch(err){toast(err.message,true);}},id==='lead-search'?220:0);});}
$('page-prev').addEventListener('click',async()=>{if(state.page>1){state.page--;await loadLeads();}});$('page-next').addEventListener('click',async()=>{if(state.page<state.pages){state.page++;await loadLeads();}});
$('export-btn').addEventListener('click',()=>{window.location.href='/api/export?'+query();});
$('telegram-test').addEventListener('click',async()=>{try{const data=await api('/api/notifications/test',{method:'POST',body:{}});toast(data.message);}catch(e){toast(e.message,true);}});
$('settings-form').addEventListener('submit',async(e)=>{e.preventDefault();try{const form=new FormData(e.target);await api('/api/settings',{method:'PATCH',body:{poll_minutes:Number(form.get('poll_minutes')),min_score:Number(form.get('min_score'))}});state.defaultMinScore=String(form.get('min_score'));state.filter.min_score=state.defaultMinScore;$('filter-score').value=state.filter.min_score;toast('Настройки сохранены');}catch(err){toast(err.message,true);}});
show('overview').catch(e=>toast(e.message,true));

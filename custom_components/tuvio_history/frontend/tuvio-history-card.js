class TuvioHistoryCard extends HTMLElement {
  constructor(){super();this.attachShadow({mode:'open'});this.page=1;this.records=[];this.selected=null;this.seq=0;this.loading=false;}
  setConfig(config){this.config=config;}
  set hass(hass){this._hass=hass;if(!this.started){this.started=true;this.render();this.load();}}
  connectedCallback(){this.timer=setInterval(()=>{if(!document.hidden&&!this.loading)this.load(false);},300000);}
  disconnectedCallback(){clearInterval(this.timer);}
  getCardSize(){return 12;}
  date(ts){return new Intl.DateTimeFormat('ru-RU',{dateStyle:'long',timeStyle:'short',timeZone:this._hass?.config?.time_zone||'Europe/Moscow'}).format(new Date(ts*1000));}
  details(record){return [record.cleaning_type,record.minutes!==undefined?`${record.minutes} мин`:null,record.area!==undefined?`${record.area} м²`:null].filter(Boolean).join(' · ')||'Сохранённая карта';}
  settings(record){return [record.cleaning_mode?`Режим: ${record.cleaning_mode}`:null,record.suction?`Мощность: ${record.suction}`:null,record.water&&record.cleaning_mode!=='Сухая'?`Подача воды: ${record.water}`:null].filter(Boolean).join(' · ');}
  async load(refresh=false){
    if(this.loading)return;this.loading=true;this.status('Загружаю историю…');this.controls();
    try{const data=await this._hass.callWS({type:'tuvio_history/list',page:this.page,refresh});this.records=data.records;this.total=data.total;this.more=data.has_more;this.updated=data.updated;
      const keep=this.records.find(r=>r.id===this.selected?.id);this.renderList();
      this.status(this.records.length?`Обновлено ${new Date(data.updated*1000).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'})}`:'Сохранённых уборок пока нет');
      if(!keep&&this.records.length)await this.select(this.records[0]);
      if(!this.records.length){this.selected=null;this.shadowRoot.querySelector('.mapbody').textContent='Карты появятся после сохранения уборки в Tuya.';}
    }catch(e){this.status(e.message||'История недоступна. Попробуйте обновить.');}
    finally{this.loading=false;this.controls();}
  }
  status(message){this.shadowRoot.querySelector('.status').textContent=message;}
  controls(){for(const b of this.shadowRoot.querySelectorAll('[data-page]'))b.disabled=this.loading||(b.dataset.page==='prev'?this.page===1:!this.more);this.shadowRoot.querySelector('.refresh').disabled=this.loading;}
  renderList(){
    const list=this.shadowRoot.querySelector('.records');list.replaceChildren();
    for(const r of this.records){const b=document.createElement('button');b.className='record';b.dataset.id=r.id;b.setAttribute('aria-pressed',String(r.id===this.selected?.id));
      const title=document.createElement('span');title.className='record-title';title.textContent=this.date(r.time);const sub=document.createElement('span');sub.className='record-sub';sub.textContent=this.details(r);b.append(title,sub);const settings=this.settings(r);if(settings){const meta=document.createElement('span');meta.className='record-settings';meta.textContent=settings;b.append(meta);}b.onclick=()=>this.select(r);list.append(b);}
    this.shadowRoot.querySelector('.count').textContent=`${this.total} записей`;
    this.shadowRoot.querySelector('.page').textContent=`Страница ${this.page}`;
  }
  async select(record){
    this.selected=record;const seq=++this.seq;for(const b of this.shadowRoot.querySelectorAll('.record'))b.setAttribute('aria-pressed',String(b.dataset.id===record.id));
    this.shadowRoot.querySelector('.mapdate').textContent=`${this.date(record.time)} · ${this.details(record)}`;this.shadowRoot.querySelector('.mapsettings').textContent=this.settings(record)||'Исторические настройки не найдены';const body=this.shadowRoot.querySelector('.mapbody');body.textContent='Загружаю карту…';body.setAttribute('aria-busy','true');
    try{const data=await this._hass.callWS({type:'tuvio_history/map',file_id:record.id});if(seq!==this.seq)return;
      const img=document.createElement('img');img.alt=`Сохранённая карта уборки от ${this.date(record.time)}`;img.src='data:image/png;base64,'+data.image;body.replaceChildren(img);
    }catch(e){if(seq===this.seq)body.textContent=e.message||'Не удалось загрузить карту.';}
    finally{if(seq===this.seq)body.setAttribute('aria-busy','false');}
  }
  render(){this.shadowRoot.innerHTML=`
    <style>
      :host{display:block;color:var(--primary-text-color);font-family:var(--paper-font-body1_-_font-family,system-ui)}
      *{box-sizing:border-box} .shell{max-width:1160px;margin:12px auto 32px;padding:0 12px}
      header{display:flex;justify-content:space-between;align-items:center;gap:16px;margin:8px 0 22px}
      h1{font-size:clamp(26px,4vw,34px);margin:0 0 7px;letter-spacing:-.6px}p{margin:0;color:var(--secondary-text-color);line-height:1.5;font-size:14px}
      button{font:inherit;cursor:pointer;color:inherit;border:1px solid var(--divider-color);background:var(--card-background-color);border-radius:13px;min-height:44px;padding:10px 14px}
      button:focus-visible{outline:3px solid var(--primary-color);outline-offset:2px}button:disabled{opacity:.45;cursor:default}
      .layout{display:grid;grid-template-columns:minmax(250px,320px) minmax(0,1fr);gap:20px;align-items:start}
      .panel{border:1px solid var(--divider-color);border-radius:22px;background:var(--card-background-color);overflow:hidden}
      .listhead,.maphead{padding:20px;border-bottom:1px solid var(--divider-color)}.count{font-weight:650;font-size:17px}.status{font-size:12px;color:var(--secondary-text-color);margin-top:6px;line-height:1.45}
      .records{max-height:560px;overflow:auto;padding:8px}.record{display:block;text-align:left;width:100%;border:0;margin:3px 0;padding:14px;min-height:70px;background:transparent}
      .record:hover{background:var(--secondary-background-color)}.record[aria-pressed=true]{background:rgba(71,116,231,.13);box-shadow:inset 3px 0 #4774e7}
      .record-title{display:block;font-size:14px;font-weight:650;line-height:1.5}.record-sub,.record-settings{display:block;font-size:12px;color:var(--secondary-text-color);margin-top:4px}.record-settings{font-size:11px}
      .pager{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:12px;border-top:1px solid var(--divider-color)}.page{font-size:12px;color:var(--secondary-text-color)}
      .maphead h2{font-size:18px;margin:0 0 7px}.mapdate{font-size:14px;color:var(--secondary-text-color)}.mapsettings{font-size:12px;color:var(--secondary-text-color);margin-top:7px}
      .mapbody{display:flex;align-items:center;justify-content:center;min-height:300px;padding:20px;text-align:center;background:#f4f7fb;color:#39485e;line-height:1.5}
      img{display:block;max-width:100%;width:100%;height:auto;max-height:580px;object-fit:contain;image-rendering:pixelated}
      .note{padding:16px 20px;font-size:13px;line-height:1.5;color:var(--secondary-text-color)}
      @media(max-width:700px){.shell{padding:0 4px;margin-top:4px}header{align-items:flex-start;margin-bottom:16px}.refresh{font-size:13px;padding:10px}.layout{grid-template-columns:minmax(0,1fr);gap:16px}.map{grid-row:1}.records{max-height:330px}.mapbody{min-height:190px;padding:12px}.maphead,.listhead{padding:16px}.note{padding:12px 16px}.maphead h2{font-size:16px}}
    </style>
    <div class="shell"><header><div><h1>История уборок</h1><p>Tuvio · сохранённые карты</p></div><button class="refresh" aria-label="Обновить историю">Обновить</button></header>
    <div class="layout"><section class="panel list" aria-label="Список уборок"><div class="listhead"><div class="count">История</div><div class="status" role="status"></div></div><div class="records"></div><div class="pager"><button data-page="prev" aria-label="Предыдущая страница">←</button><span class="page">Страница 1</span><button data-page="next" aria-label="Следующая страница">→</button></div></section>
    <section class="panel map" aria-label="Карта выбранной уборки"><div class="maphead"><h2>Карта уборки</h2><div class="mapdate">Выберите запись</div><div class="mapsettings"></div></div><div class="mapbody" aria-live="polite">Загружаю историю…</div><div class="note">Синяя линия — сохранённый маршрут робота. Вид уборки восстановлен по журналу Tuya, режим и мощность — по истории Home Assistant. Дата — время сохранения файла.</div></section></div></div>`;
    this.shadowRoot.querySelector('.refresh').onclick=()=>this.load(true);
    for(const button of this.shadowRoot.querySelectorAll('[data-page]'))button.onclick=()=>{this.page+=button.dataset.page==='next'?1:-1;this.load();};
  }
}
if(!customElements.get('tuvio-history-card'))customElements.define('tuvio-history-card',TuvioHistoryCard);
window.customCards=window.customCards||[];window.customCards.push({type:'tuvio-history-card',name:'Tuvio · История уборок',description:'История Tuya с картами, маршрутами и типами уборки'});

/* -------------------------------------------------------
   Flip cards — Before/After (overlay + blur + lock)
------------------------------------------------------- */
(function () {
  const cards = [...document.querySelectorAll('[data-flip]')];
  if (!cards.length) return;

  // Overlay
  let overlay = document.querySelector('.flip-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.className = 'flip-overlay';
    document.body.appendChild(overlay);
  }

  let openCard = null;
  let lastTrigger = null;

  const lock = () => document.body.classList.add('is-locked');
  const unlock = () => document.body.classList.remove('is-locked');

  function open(card, trigger) {
    if (openCard && openCard !== card) closeOpen({ restoreFocus:false });

    openCard = card;
    lastTrigger = trigger || card;

    card.classList.add('is-open');
    card.setAttribute('aria-expanded','true');
    overlay.classList.add('is-visible');
    lock();

    const back = card.querySelector('.flip-back');
    if (back) { back.setAttribute('tabindex','-1'); back.focus(); }
  }

  function closeOpen({ restoreFocus=true } = {}) {
    if (!openCard) return;
    openCard.classList.remove('is-open');
    openCard.setAttribute('aria-expanded','false');
    overlay.classList.remove('is-visible');
    unlock();

    if (restoreFocus && lastTrigger) {
      try { lastTrigger.focus(); } catch (e) {}
    }
    openCard = null;
    lastTrigger = null;
  }

  // Bind
  cards.forEach(card => {
    const front = card.querySelector('[data-flip-front]');
    const plus  = card.querySelector('.poster-plus');
    const close = card.querySelector('.poster-close');
    const back  = card.querySelector('.flip-back');

    if (front) front.addEventListener('click', () => open(card, front));
    if (plus)  plus.addEventListener('click', (e)=>{ e.stopPropagation(); open(card, plus); });
    if (back)  back.addEventListener('click', (e)=>{ if (!e.target.closest('.no-close')) closeOpen(); });
    if (close) close.addEventListener('click', (e)=>{ e.stopPropagation(); closeOpen(); });
  });

  // Close with Esc
  document.addEventListener('keydown', (e)=>{
    if (e.key === 'Escape') closeOpen();
  });

  // Overlay click closes
  overlay.addEventListener('click', ()=> closeOpen());

  // Any scroll while open -> close
  let lastY = window.scrollY;
  window.addEventListener('scroll', ()=>{
    if (!openCard) { lastY = window.scrollY; return; }
    if (Math.abs(window.scrollY - lastY) > 0) closeOpen();
    lastY = window.scrollY;
  }, { passive:true });
})();

/* -------------------------------------------------------
   Flip cards — How it works (no overlay)
------------------------------------------------------- */
(function () {
  const cards = [...document.querySelectorAll('[data-flip-lite]')];
  if (!cards.length) return;

  let openCard = null;

  function open(card){
    if (openCard && openCard !== card){
      openCard.classList.remove('is-open');
      openCard.setAttribute('aria-expanded','false');
    }
    openCard = card;
    card.classList.add('is-open');
    card.setAttribute('aria-expanded','true');
    const back = card.querySelector('.flip-back');
    if (back){ back.setAttribute('tabindex','-1'); back.focus(); }
  }
  function close(card){
    (card || openCard)?.classList.remove('is-open');
    (card || openCard)?.setAttribute('aria-expanded','false');
    if (!card) openCard = null;
  }

  cards.forEach(card=>{
    const front = card.querySelector('[data-flip-front]');
    const plus  = card.querySelector('.poster-plus');
    const closeBtn = card.querySelector('.poster-close');
    const back  = card.querySelector('.flip-back');

    if (front) front.addEventListener('click', ()=> open(card));
    if (plus)  plus.addEventListener('click',(e)=>{ e.stopPropagation(); open(card); });
    if (back)  back.addEventListener('click',(e)=>{ if (!e.target.closest('.no-close')) close(card); });
    if (closeBtn) closeBtn.addEventListener('click',(e)=>{ e.stopPropagation(); close(card); });
  });

  document.addEventListener('keydown',(e)=>{ if (e.key==='Escape') close(); });
})();

/* -------------------------------------------------------
   ROI calculator (live) — no “based on …” line
------------------------------------------------------- */
(function (){
  const elR = document.getElementById('range_reviews');
  const elM = document.getElementById('range_min');
  const elH = document.getElementById('range_rate');
  const outR= document.getElementById('out_reviews');
  const outM= document.getElementById('out_min');
  const outH= document.getElementById('out_rate');
  const money = document.getElementById('kpi_money');
  const time  = document.getElementById('kpi_time');
  if (!(elR && elM && elH)) return;

  const f0 = n => new Intl.NumberFormat('en-US',{maximumFractionDigits:0}).format(n);
  const f1 = n => new Intl.NumberFormat('en-US',{maximumFractionDigits:1}).format(n);

  function compute(){
    const r = +elR.value, m = +elM.value, h = +elH.value;
    outR.textContent=r; outM.textContent=m; outH.textContent=h;

    // Approximate weekly hours saved
    let hrs = (r*m/60)*7 + 3.0 - 2.0;
    if (hrs < 0) hrs = 0;

    const eurosMonth = hrs*h*4.33;
    money.textContent = `${f0(eurosMonth)} €`;
    time.textContent  = `${f1(hrs)} hrs/week saved`;
  }
  [elR,elM,elH].forEach(i=> i.addEventListener('input', compute));
  compute();
})();

/* -------------------------------------------------------
   Subtle hero parallax
------------------------------------------------------- */
(function(){
  const bg=document.querySelector('.hero__bg');
  if(!bg) return;
  window.addEventListener('scroll',()=>{
    const y=Math.min(120, window.scrollY/6);
    bg.style.transform=`translateY(${y}px)`;
  },{passive:true});
})();

/* -------------------------------------------------------
   Demo form — generate preview via /api/report + export PDF
------------------------------------------------------- */
(function(){
  const cfg = (window.__INSIGHTSCFG__ && window.__INSIGHTSCFG__.endpoints)
    ? window.__INSIGHTSCFG__.endpoints
    : { generate: '/api/report', pdf: '/api/report/pdf' };

  const form   = document.getElementById('demo_form');
  const nameEl = document.getElementById('hotel_name');
  const cityEl = document.getElementById('hotel_city');
  const btnGen = document.getElementById('btn_generate');
  const btnPdf = document.getElementById('btn_pdf');
  const result = document.getElementById('demo_result');
  const status = document.getElementById('demo_status');
  const error  = document.getElementById('demo_error');
  if(!form || !nameEl || !cityEl || !result || !status || !error) return;

  // initial state
  if (btnPdf) btnPdf.hidden = true;
  form.setAttribute('novalidate', 'true');

  let ctrl = null;

  function showError(msg){
    error.textContent = msg || '';
    error.hidden = !msg;
  }
  function showStatus(flag){
    status.hidden = !flag; // content already in HTML (spinner + text)
    if (flag) status.setAttribute('aria-busy','true');
    else status.removeAttribute('aria-busy');
  }
  function resetPreview(){
    result.innerHTML = '';
  }

  async function onSubmit(e){
    if (e && typeof e.preventDefault === 'function') e.preventDefault();

    const hotel = (nameEl.value || '').trim();
    const city  = (cityEl.value || '').trim();

    showError('');
    resetPreview();
    showStatus(true);

    if (!hotel || !city){
      showStatus(false);
      showError('Please provide a name and a city.');
      if (btnPdf) btnPdf.hidden = true;
      return;
    }

    const originalText = btnGen?.getAttribute('data-original-text') || btnGen?.textContent || 'Generate report';
    const loadingText  = btnGen?.getAttribute('data-loading-text') || 'Generating…';

    if (btnGen){
      btnGen.disabled = true;
      btnGen.textContent = loadingText;
    }
    if (btnPdf){
      btnPdf.disabled = true;
      btnPdf.hidden = true;
    }

    try{
      if(ctrl) ctrl.abort();
      ctrl = new AbortController();
      const r = await fetch(cfg.generate, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hotel, city }),
        signal: ctrl.signal
      });
      if(!r.ok){
        const t = await r.text().catch(()=> '');
        throw new Error(t || ('HTTP '+r.status));
      }
      const data = await r.json().catch(()=> ({}));
      const html = (data && data.html) ? String(data.html) : '';
      if (html){
        result.innerHTML = html;
        showError('');
        if (btnPdf){
          btnPdf.hidden = false;
          btnPdf.disabled = false;
        }
      } else {
        result.innerHTML = '';
        showError('No content returned by the server.');
        if (btnPdf) btnPdf.hidden = true;
      }
    } catch(err){
      console.error(err);
      result.innerHTML = '';
      showError('⚠️ Unable to generate. Please try again.');
      if (btnPdf) btnPdf.hidden = true;
    } finally {
      showStatus(false);
      if (btnGen){
        btnGen.disabled = false;
        btnGen.textContent = originalText;
      }
    }
  }

  // Store original button text for reset
  if (btnGen && !btnGen.getAttribute('data-original-text')) {
    btnGen.setAttribute('data-original-text', btnGen.textContent || 'Generate report');
  }

  // Bind both: click and (defensive) submit
  btnGen?.addEventListener('click', onSubmit);
  form.addEventListener('submit', onSubmit);

  // Server-side PDF generation (no html2canvas/jsPDF required)
  btnPdf?.addEventListener('click', async ()=>{
    const hotel = (nameEl.value || '').trim();
    const city  = (cityEl.value || '').trim();
    if(!hotel || !city) return;

    btnPdf.disabled = true;
    try{
      const r = await fetch(cfg.pdf, {
        method:'POST',
        headers:{ 'Content-Type':'application/json' },
        body: JSON.stringify({ hotel, city })
      });
      if(!r.ok) throw new Error('HTTP '+r.status);
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'InsightsAI-report.pdf';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch(e){
      console.error(e);
      showError('Unable to download PDF right now.');
    } finally {
      btnPdf.disabled = false;
    }
  });
})();